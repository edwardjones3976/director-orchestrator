# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:38 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:01 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 21:18 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:27 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 07:05 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:43 UTC | BEGIN]]
# [[Grok | grok-4.6 | 2026-09-22 | 22:10 UTC | BEGIN]]
# Material repair and regression authored by Grok; applied by Codex from the retained provider response.
# Codex QA aligned the existing reconciliation test double with the occupied() interface.
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 17:22 UTC | BEGIN]]
# [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-22 | 16:58 UTC | BEGIN]]
# [[Codex Director integration | GPT-6 Astra | 2026-09-22 | 01:54 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 12:57 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 12:05 UTC | BEGIN]]
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 01:53 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-22 | 01:35 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-22 | 00:15 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 23:25 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 22:45 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:45 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-14 | 06:14 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 18:45 UTC | BEGIN]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-13 | 03:50 UTC | BEGIN]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-13 | 02:45 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:04 UTC | BEGIN]]
# [[Claude Code mechanical merge | Claude Opus 5 | 2026-09-12 | 02:56 UTC | BEGIN]]
# [[Gemini Antigravity | Gemini 3.1 Pro (High) | 2026-09-11 | 19:26 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-10 | 18:17 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 04:06 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 07:16 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:37 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:22 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 01:02 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-04 | 23:51 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:14 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:19 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 07:30 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 07:22 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:40 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:12 UTC | BEGIN]]
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
import re
import unittest
from functools import partial
from unittest.mock import patch
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from director_orchestrator.config import Settings
from director_orchestrator.delegation import GRANT_SCHEMA_VERSION, Tier1Class
from director_orchestrator.delegation_ledger import (
    AUDIT_SCHEMA_VERSION,
    DelegationAuditRecord,
    GateResult,
    GateStatus,
    RecordedOutcome,
)
from director_orchestrator.store import TaskStore
from director_orchestrator.langgraph_postgres import open_postgres_control_plane
from director_orchestrator.langgraph_primary import (
    ApprovalDecision,
    ApprovalKind,
    ApprovalResume,
    HandlerContext,
    ProviderDisposition,
    ProviderSubgraphResult,
)
from director_orchestrator.langgraph_runtime import PacketBoundaryAdapter
from director_orchestrator.models import (
    AssignmentKind,
    AssignmentOutcome,
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
from director_orchestrator.primary_effect_gateways import (
    ExactEffectGatewayRegistry,
    GITHUB_MARK_READY,
    SLACK_POST_MESSAGE,
)
from director_orchestrator.primary_parallel import (
    ParallelAssignmentStatus,
    ParallelReservation,
    ParallelDispatchError,
    ParallelDispatchOutcome,
    PostgresParallelDispatch,
    ProviderAttemptReservation,
    ProviderTransientDispatchError,
    PROVIDER_ATTEMPT_BUDGET_EXHAUSTED,
    PROVIDER_RETRY_REQUIRES_TRANSIENT_FAILURE,
    PROVIDER_TRANSIENT_TRANSPORT_FAILURE,
)
from director_orchestrator.primary_default import (
    _SIMULATION_LABEL,
    DefaultDryRunServices,
    DefaultOfflineSimulationServices,
    DefaultPrimaryActivation,
    build_offline_parallel_dispatcher,
    DefaultPrimaryApplication,
    DefaultPrimaryAuthority,
    _assignment_provider_lane,
    _execute_three_attempt_roster,
    _live_parallel_plan,
    _primary_provider_lane,
    _terminal_review_assignment,
    _typed_effect_binding,
    build_automatic_primary_live_dependencies,
    build_primary_packet_result_resolver,
    build_typed_external_effect_grant,
    resolve_default_primary_authority,
    small_pre_authorization_refusal,
    small_producer_result_refusal,
    small_review_bounds,
    typed_external_effect_grant_kinds,
    _small_producer_constraints,
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


# The preview plan's producer and reviewer lanes, as compact-view transports.
SMALL_LANES = frozenset({"claude", "cursor"})


class SmallReviewBoundsTests(unittest.TestCase):
    """The reviewer of an eligible small read-only task is capped deterministically."""

    SMALL = "Project: default\nSummarize README.md for the owner."

    def setUp(self) -> None:
        self.settings = replace(
            Settings(), repository_allowlist=("owner/director-orchestrator",)
        )
        self.router = packet_router(self.settings)

    def record(self, result: str, *, text: str = SMALL) -> TaskRecord:
        record = TaskRecord(
            task_id="DIR-LG-SMALLREVIEW",
            status=TaskStatus.RUNNING,
            intake=SlackIntake(
                source_event_id="event-small-review",
                channel_id="CDEFAULT",
                thread_ts="1720000000.000001",
                user_id="UOWNER",
                text=text,
            ),
        )
        record.plan = preview_plan(text, self.settings)
        producer = min(record.plan.assignments, key=lambda item: item.sequence)
        record.assignment_outcomes.append(
            AssignmentOutcome(
                assignment_sequence=producer.sequence,
                provider=producer.agent,
                run_id="run-1",
                result=result,
                review_summary="accepted producer evidence",
            )
        )
        return record

    def bounds(self, record: TaskRecord, *, reviewer: bool = True, lanes=SMALL_LANES):
        pick = max if reviewer else min
        assignment = pick(record.plan.assignments, key=lambda item: item.sequence)
        packet = self.router.planned_work_packet(record, assignment)
        return small_review_bounds(
            record, self.settings, assignment, packet, compact_view_lanes=lanes)

    def test_an_oversized_review_packet_is_refused(self):
        self.assertEqual(
            ("small_task_review_packet_over_cap", 40_000),
            self.bounds(self.record("x" * 20_000)),
        )

    def test_a_small_review_carries_the_configured_token_cap(self):
        self.assertEqual((None, 40_000), self.bounds(self.record("done")))
        capped = replace(self.settings, small_task_review_max_tokens=1_234)
        record = self.record("done")
        review = max(record.plan.assignments, key=lambda item: item.sequence)
        packet = self.router.planned_work_packet(record, review)
        self.assertEqual((None, 1_234), small_review_bounds(
            record, capped, review, packet, compact_view_lanes=SMALL_LANES))

    def test_the_producer_and_ineligible_tasks_are_not_bounded(self):
        self.assertEqual((None, None), self.bounds(self.record("done"), reviewer=False))
        ineligible = self.record(
            "done", text="Project: default\nCreate a bounded implementation plan."
        )
        self.assertEqual((None, None), self.bounds(ineligible))

    def test_a_full_packet_reviewer_lane_is_outside_the_class(self):
        # The cursor host-bridge lane posts the whole packet, so its reviewer is
        # never measured on a compact view it is not sent: no class, no cap.
        self.assertEqual((None, None), self.bounds(self.record("done"), lanes={"claude"}))
        self.assertEqual((None, None), self.bounds(self.record("x" * 20_000), lanes={"claude"}))

    def producer_refusal(self, outcome: str, record: TaskRecord | None = None):
        record = record or self.record("unused")
        record.assignment_outcomes = []
        producer = min(record.plan.assignments, key=lambda item: item.sequence)
        return small_producer_result_refusal(
            record, self.settings, producer,
            {"run_reference": "run-1", "outcome": outcome}, "sha256:" + "e" * 64,
            self.router, compact_view_lanes=SMALL_LANES,
        )

    def test_a_producer_result_is_bounded_before_any_reviewer_work(self):
        """The producer check and the reviewer check agree to the byte."""

        self.assertIsNone(self.producer_refusal("done"))
        low, high = 1, 20_000
        while low < high:
            middle = (low + high + 1) // 2
            if self.producer_refusal("x" * middle) is None:
                low = middle
            else:
                high = middle - 1
        self.assertEqual("small_task_producer_result_over_cap",
                         self.producer_refusal("x" * (low + 1)))
        # A result exactly at the limit still reaches its reviewer uncapped.
        record = self.record("x" * low)
        record.assignment_outcomes[0].review_summary = "x" * low
        record.assignment_outcomes[0].provider_result_digest = "e" * 64
        self.assertEqual((None, 40_000), self.bounds(record))
        record.assignment_outcomes[0].result = record.assignment_outcomes[0].review_summary = (
            "x" * (low + 1))
        self.assertEqual("small_task_review_packet_over_cap", self.bounds(record)[0])
        # Quotes and newlines are escaped twice, so they fit fewer characters.
        self.assertEqual("small_task_producer_result_over_cap",
                         self.producer_refusal('"\n' * (low // 2)))
        # Past the outcome model's own bounds it is refused, not raised.
        self.assertEqual("small_task_producer_result_over_cap",
                         self.producer_refusal("x" * 60_000))
        # Every other task and the reviewer itself are unaffected.
        ineligible = self.record(
            "unused", text="Project: default\nCreate a bounded implementation plan.")
        self.assertIsNone(self.producer_refusal("x" * 60_000, ineligible))

    def test_the_producer_is_told_the_ceiling_before_dispatch(self):
        hinted = DirectorPacketTaskRouter(
            authority_snapshots=_FixedAuthorityResolver(),
            boundary_adapters={},
            writer_owner_id="langgraph-primary-default",
            writer_secret="default-primary-test-writer-secret",
            timeout_seconds=self.settings.provider_timeout_seconds,
            extra_constraints=partial(
                _small_producer_constraints, self.settings, compact_view_lanes=SMALL_LANES),
        )
        record = self.record("unused")
        record.assignment_outcomes = []
        producer = min(record.plan.assignments, key=lambda item: item.sequence)
        [hint] = [item for item in hinted.planned_work_packet(record, producer)["constraints"]
                  if item.startswith("Small read-only task:")]
        ceiling = int(re.search(r"under (\d+) characters", hint).group(1))
        self.assertGreater(ceiling, 0)
        # The advertised ceiling holds for the costliest characters it allows:
        # quotes and backslashes, newlines and tabs, and non-ASCII text.
        for character in ("x", '"', "\\", "\n", "\t", "\u00e9", "\u4e2d", "\U0001f600"):
            with self.subTest(character=character):
                self.assertIsNone(self.producer_refusal(character * ceiling))
        # A task outside the class under the activation's lanes is not told:
        # nothing would refuse its outcome (the cursor bridge posts the full packet).
        unhinted = DirectorPacketTaskRouter(
            authority_snapshots=_FixedAuthorityResolver(),
            boundary_adapters={},
            writer_owner_id="langgraph-primary-default",
            writer_secret="default-primary-test-writer-secret",
            timeout_seconds=self.settings.provider_timeout_seconds,
            extra_constraints=partial(
                _small_producer_constraints, self.settings, compact_view_lanes={"claude"}),
        )
        self.assertFalse(any(item.startswith("Small read-only task:") for item in
                             unhinted.planned_work_packet(record, producer)["constraints"]))
        # Deterministic, so the approval packet and the dispatched one agree.
        self.assertEqual(hinted.planned_work_packet(record, producer),
                         hinted.planned_work_packet(record, producer))
        ineligible = self.record(
            "unused", text="Project: default\nCreate a bounded implementation plan.")
        ineligible.assignment_outcomes = []
        first = min(ineligible.plan.assignments, key=lambda item: item.sequence)
        self.assertFalse(any(item.startswith("Small read-only task:") for item in
                             hinted.planned_work_packet(ineligible, first)["constraints"]))


class _MemoryAuditAnchor:
    """Test double only: production must supply a durable, separately protected anchor."""

    def __init__(self):
        self.head = (0, "0" * 64)

    def read_head(self):
        return self.head

    def compare_and_set(self, expected, updated):
        if self.head != expected:
            raise RuntimeError("anchor changed")
        self.head = updated


class _UnreadableAuditAnchor(_MemoryAuditAnchor):
    def read_head(self):
        raise OSError("anchor unavailable")


SMALL_GRANT = {
    "schema_version": GRANT_SCHEMA_VERSION,
    "grant_id": "grant-small-1",
    "repository": "owner/director-orchestrator",
    "branch_pattern": "main",
    "classes": ["execute_small_read_only_task"],
    "providers": ["claude", "cursor"],
    "expires_at": "2099-01-01T00:00:00+00:00",
    "max_exercises": 5,
    "revoked": False,
}
SMALL_TEXT = "Project: default\nSummarize README.md for the owner."
TERM_ORDER = [
    "authorization", "eligibility", "bounds", "verification",
    "lane_independence", "safe_stopping", "owned_paths_declared",
]


RUNNING = {"terminal_classification": None, "parallel_plan_digest": digest("parallel-plan")}


def small_grant(**changes) -> str:
    return json.dumps({**SMALL_GRANT, **changes})


def audit_ledger(directory: str) -> TaskStore:
    return TaskStore(
        str(Path(directory) / f"audit-{uuid.uuid4().hex}.sqlite3"),
        delegation_audit_anchor=_MemoryAuditAnchor(),
    )


def gate_statuses(entry) -> dict:
    return {item.gate: item.status for item in entry.gate_results}


class StandingGrantExerciseTests(unittest.TestCase):
    """The tier-1 class execute_small_read_only_task, evaluated before execution."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = directory.name
        self.settings = replace(
            Settings(),
            app_env="default-test",
            allow_paid_runs=False,
            project_allowlist=("default",),
            repository_allowlist=("owner/director-orchestrator",),
            allowed_starting_refs=("main",),
            slack_owner_user_id="UOWNER",
            slack_channel_id="CDIRECTOR",
            delegation_enabled=True,
            delegation_suspended=False,
            delegation_grant_json=small_grant(),
        )
        self.ledger = audit_ledger(self.directory)
        self.sequence = 0

    def application(self, ledger="default", *, dispatcher=True, lanes=SMALL_LANES, **changes):
        return DefaultPrimaryApplication(
            replace(self.settings, **changes),
            # Prior pre-authorized tasks read as never started unless a test
            # supplies their durable state.
            self.coordinator(None),
            None,
            DefaultPrimaryAuthority(SHA, digest("authority")),
            parallel_dispatcher=(lambda reservation: None) if dispatcher else None,
            delegation_ledger=self.ledger if ledger == "default" else ledger,
            compact_view_lanes=lanes,
        )

    def record(self, text=SMALL_TEXT, *, user="UOWNER", channel="CDIRECTOR",
               authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED):
        self.sequence += 1
        record = TaskRecord(
            task_id=f"DIR-LG-GRANT{self.sequence:04d}",
            status=TaskStatus.QUEUED,
            intake=SlackIntake(
                source_event_id=f"event-grant-{self.sequence}",
                channel_id=channel,
                thread_ts="1720000000.000001",
                user_id=user,
                text=text,
            ),
            intake_authentication=authentication,
        )
        record.plan = preview_plan(text, self.settings)
        return record

    @staticmethod
    def exercise(application, record) -> bool:
        return application._exercise_standing_grant(
            record, attested=application._attested_owner_intake(record)
        )

    def audit(self, ledger=None):
        return (ledger or self.ledger).delegation_audit_records(limit=1_000)

    def test_eligible_owner_task_is_pre_authorized_with_one_audit_record(self):
        record = self.record()
        self.assertTrue(self.exercise(self.application(), record))
        [entry] = self.audit()
        self.assertIs(RecordedOutcome.PRE_AUTHORIZED, entry.outcome)
        self.assertIs(Tier1Class.EXECUTE_SMALL_READ_ONLY_TASK, entry.decision_class)
        self.assertEqual("grant-small-1", entry.grant_id)
        self.assertEqual((), entry.verdicts)
        self.assertEqual(TERM_ORDER, [item.gate for item in entry.gate_results])
        self.assertTrue(all(item.status is GateStatus.PASS for item in entry.gate_results))
        self.assertEqual(SHA, entry.candidate_sha)
        self.assertTrue(entry.acting_agent.endswith(record.task_id))

    def test_no_live_configured_grant_for_the_class_writes_nothing(self):
        cases = {
            "absent": {"delegation_grant_json": ""},
            "malformed": {"delegation_grant_json": "{not json"},
            "disabled": {"delegation_enabled": False},
            "suspended": {"delegation_suspended": True},
            "other_class": {"delegation_grant_json": small_grant(
                classes=["mark_pull_request_ready_for_review"])},
        }
        for name, changes in cases.items():
            with self.subTest(case=name):
                self.assertFalse(self.exercise(self.application(**changes), self.record()))
        self.assertEqual([], self.audit())

    def test_expired_revoked_or_exhausted_grant_is_refused_and_audited(self):
        for name, grant in {
            "expired": small_grant(expires_at="2000-01-01T00:00:00+00:00"),
            "revoked": small_grant(revoked=True),
        }.items():
            with self.subTest(case=name):
                ledger = audit_ledger(self.directory)
                application = self.application(ledger, delegation_grant_json=grant)
                self.assertFalse(self.exercise(application, self.record()))
                [entry] = self.audit(ledger)
                self.assertIs(RecordedOutcome.REFUSED, entry.outcome)
                self.assertIs(GateStatus.FAIL, gate_statuses(entry)["authorization"])

        application = self.application(delegation_grant_json=small_grant(max_exercises=1))
        self.assertTrue(self.exercise(application, self.record()))
        self.assertFalse(self.exercise(application, self.record()))
        first, second = self.audit()
        self.assertIs(RecordedOutcome.PRE_AUTHORIZED, first.outcome)
        self.assertIs(RecordedOutcome.REFUSED, second.outcome)
        self.assertIs(GateStatus.FAIL, gate_statuses(second)["authorization"])

    def test_non_owner_foreign_channel_or_unattested_intake_is_refused(self):
        application = self.application()
        for name, record in {
            "non_owner": self.record(user="UOTHER"),
            "foreign_channel": self.record(channel="COTHER"),
            "bearer": self.record(authentication=IntakeAuthentication.BEARER_DRY_RUN),
        }.items():
            with self.subTest(case=name):
                self.assertFalse(application._attested_owner_intake(record))
                self.assertFalse(self.exercise(application, record))
        self.assertFalse(application._exercise_standing_grant(self.record(), attested=False))
        entries = self.audit()
        self.assertEqual(4, len(entries))
        for entry in entries:
            self.assertIs(RecordedOutcome.REFUSED, entry.outcome)
            self.assertIs(GateStatus.FAIL, gate_statuses(entry)["authorization"])

    def test_an_ineligible_task_is_refused_and_audited(self):
        for application, record in (
            (self.application(), self.record(
                "Project: default\nCreate a bounded implementation plan.")),
            # The reviewer's cursor lane posts the full packet: outside the class.
            (self.application(lanes={"claude"}), self.record()),
        ):
            self.assertFalse(self.exercise(application, record))
        for entry in self.audit():
            self.assertIs(RecordedOutcome.REFUSED, entry.outcome)
            # Refused on eligibility alone; no other term is claimed evaluated.
            self.assertEqual({"eligibility": GateStatus.FAIL}, gate_statuses(entry))
        self.assertEqual(2, len(self.audit()))

    def test_no_record_claims_the_candidate_bound_gates_or_independence(self):
        self.assertTrue(self.exercise(self.application(), self.record()))
        self.assertFalse(self.exercise(self.application(), self.record(user="UOTHER")))
        for entry in self.audit():
            self.assertFalse({"gates", "independence", "scope"} & set(gate_statuses(entry)))

    def test_an_absent_or_unreadable_ledger_latches_attended(self):
        application = self.application(ledger=None)
        self.assertFalse(self.exercise(application, self.record()))
        application._delegation_ledger = self.ledger
        self.assertFalse(self.exercise(application, self.record()))
        self.assertEqual([], self.audit())

        unreadable = TaskStore(
            str(Path(self.directory) / "unreadable.sqlite3"),
            delegation_audit_anchor=_UnreadableAuditAnchor(),
        )
        application = self.application(unreadable)
        self.assertFalse(self.exercise(application, self.record()))
        self.assertTrue(application._delegation_tripped)

    def test_a_recorded_trip_suspends_only_that_grant_id(self):
        now = datetime.now(timezone.utc)
        self.ledger.append_delegation_audit_record(DelegationAuditRecord(
            schema_version=AUDIT_SCHEMA_VERSION,
            outcome=RecordedOutcome.REFUSED,
            grant_id="grant-small-1",
            grant_snapshot=self.settings.delegation_grant_json,
            decision_class=Tier1Class.EXECUTE_SMALL_READ_ONLY_TASK,
            candidate_sha=SHA,
            acting_agent="director-langgraph:DIR-LG-EARLIER",
            verdicts=(),
            gate_results=(GateResult("delegation_trip", GateStatus.FAIL, now),),
            recorded_at=now,
        ))
        self.assertFalse(self.exercise(self.application(), self.record()))
        self.assertIs(GateStatus.FAIL, gate_statuses(self.audit()[-1])["authorization"])
        # Only an explicit owner action, a new grant_id, clears it.
        renewed = self.application(delegation_grant_json=small_grant(grant_id="grant-small-2"))
        self.assertTrue(self.exercise(renewed, self.record()))

    def test_a_grant_never_reaches_past_its_repository_branch_lanes_or_holds(self):
        refused = {
            "foreign_repository": {"delegation_grant_json": small_grant(
                repository="owner/other-repository")},
            "branch_not_covered": {"delegation_grant_json": small_grant(
                branch_pattern="release/*")},
            "lane_not_named": {"delegation_grant_json": small_grant(
                providers=["claude", "grok"])},
        }
        for name, changes in refused.items():
            with self.subTest(case=name):
                ledger = audit_ledger(self.directory)
                self.assertFalse(self.exercise(self.application(ledger, **changes), self.record()))
                [entry] = self.audit(ledger)
                self.assertIs(RecordedOutcome.REFUSED, entry.outcome)
                self.assertIs(GateStatus.FAIL, gate_statuses(entry)["authorization"])
        with self.subTest(case="unbound_starting_commit"):
            ledger = audit_ledger(self.directory)
            application = self.application(ledger)
            application.authority = DefaultPrimaryAuthority("not-a-sha", digest("authority"))
            self.assertFalse(self.exercise(application, self.record()))
            [entry] = self.audit(ledger)
            self.assertIs(GateStatus.FAIL, gate_statuses(entry)["verification"])
        with self.subTest(case="workload_hold"):
            ledger = audit_ledger(self.directory)
            record = self.record()
            record.workload_selection_hold_reasons = ["workload_hold"]
            self.assertFalse(self.exercise(self.application(ledger), record))
            self.assertEqual([], self.audit(ledger))

    def test_an_unattested_intake_is_refused_without_reading_history(self):
        application = self.application()
        with patch.object(self.ledger, "delegation_audit_records",
                          side_effect=AssertionError("history read")):
            self.assertFalse(self.exercise(application, self.record(user="UOTHER")))
        self.assertFalse(application._delegation_tripped)
        [entry] = self.audit()
        self.assertEqual({"authorization": GateStatus.FAIL}, gate_statuses(entry))

    def test_health_reports_4s_only_when_the_application_can_pre_authorize(self):
        import importlib

        main = importlib.import_module("director_orchestrator.main")
        cases = {
            "capable": (self.application(), True),
            "no_ledger": (self.application(None), False),
            "no_compact_lane": (self.application(lanes=frozenset()), False),
            # A small task needs a producer and an independent reviewer.
            "one_compact_lane": (self.application(lanes=frozenset({"claude"})), False),
            "one_family": (self.application(lanes=frozenset({"grok", "grok-bot"})), False),
            "paid_off": (self.application(
                dispatcher=False,
                langgraph_primary_offline_simulation_enabled=False,
                langgraph_primary_live_provider_dispatch_enabled=False), False),
        }
        for name, (application, armed) in cases.items():
            with self.subTest(case=name), patch.object(
                main, "primary_default_activation",
                SimpleNamespace(application=application, live_dependencies=None,
                                health=lambda: {}),
            ), patch.object(main, "settings", replace(
                main.settings, delegation_enabled=True, delegation_suspended=False,
                delegation_grant_json=small_grant())):
                delegation = main.health()["delegation"]
                self.assertIs(armed, delegation["pre_execution_armed"])
                self.assertIs(armed, delegation["executes_decisions"])
        latched = self.application()
        latched._delegation_tripped = True
        self.assertFalse(latched.pre_execution_capable())

    def test_an_in_flight_replay_never_exercises_the_grant(self):
        """A retry that finds the claim before its projection keeps the button."""

        application = self.application()
        requests = []

        class Stop(Exception):
            pass

        def create_intake(request):
            requests.append(request)
            raise Stop

        application.coordinator = SimpleNamespace(
            claim_authenticated_intake=lambda **_: ("DIR-LG-INFLIGHT", False),
            get_projection=lambda task_id: None,
            create_intake=create_intake,
        )
        application.services = SimpleNamespace(
            _live_composition=False, register=lambda *_, **__: None,
            discard=lambda *_: None)
        record = self.record()
        with patch.object(application, "_exercise_standing_grant",
                          side_effect=AssertionError("grant exercised")), \
                self.assertRaises(Stop):
            application.create_intake(
                record.intake,
                intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
                authenticated_replay_identity=("a" * 64, "b" * 64),
            )
        [request] = requests
        self.assertEqual("DIR-LG-INFLIGHT", request.task_id)
        self.assertTrue(request.execution_approval_required)
        self.assertEqual([], self.audit())

    def test_a_pre_authorized_task_that_no_longer_qualifies_is_refused(self):
        record = self.record()
        self.assertTrue(self.exercise(self.application(), record))
        stricter = replace(self.settings, small_task_review_max_tokens=0)

        def refusal(ledger, settings=self.settings, lanes=SMALL_LANES, *, approval_free=True):
            return small_pre_authorization_refusal(
                record, settings, ledger, approval_free=approval_free,
                compact_view_lanes=lanes)

        # Still in the class with its ledger: its bounds apply, nothing is refused.
        self.assertIsNone(refusal(self.ledger))
        # Restarted under a stricter reviewer cap, or with a full-packet lane.
        self.assertEqual("small_task_pre_authorization_lapsed", refusal(self.ledger, stricter))
        self.assertEqual("small_task_pre_authorization_lapsed",
                         refusal(self.ledger, lanes={"claude"}))
        # Restarted with the ledger omitted or unreadable: refused even when
        # the task still qualifies, since its trips could not be recorded.
        self.assertEqual("small_task_pre_authorization_unaudited", refusal(None))
        self.assertEqual("small_task_pre_authorization_unaudited", refusal(None, stricter))
        with patch.object(self.ledger, "delegation_audit_records",
                          side_effect=OSError("ledger unavailable")):
            self.assertEqual("small_task_pre_authorization_unaudited", refusal(self.ledger))
        # A task whose graph keeps the button is never affected and never
        # reads the ledger, with or without one.
        with patch.object(self.ledger, "delegation_audit_records",
                          side_effect=AssertionError("ledger read")):
            for ledger in (None, self.ledger):
                for settings in (self.settings, stricter):
                    self.assertIsNone(refusal(ledger, settings, approval_free=False))

    def test_the_approval_free_flag_survives_the_application_projection(self):
        """Recovery rehydrates it; a projection written before the grant keeps the button."""

        services = DefaultDryRunServices(
            self.settings, packet_router=packet_router(self.settings))
        granted, ordinary = self.record(), self.record()
        services.register(granted, approval_free=True)
        services.register(ordinary)
        self.assertTrue(services.execution_approval_free(granted.task_id))
        self.assertFalse(services.execution_approval_free(ordinary.task_id))
        views = {task_id: services.application_projection(task_id)
                 for task_id in (granted.task_id, ordinary.task_id)}
        self.assertIs(False, views[granted.task_id]["execution_approval_required"])
        self.assertIs(True, views[ordinary.task_id]["execution_approval_required"])
        legacy = dict(views[ordinary.task_id])
        del legacy["execution_approval_required"]
        for task_id, view, expected in (
            (granted.task_id, views[granted.task_id], True),
            (ordinary.task_id, views[ordinary.task_id], False),
            (ordinary.task_id, legacy, False),
        ):
            with self.subTest(task_id=task_id, keys=sorted(view)):
                services.discard(task_id)
                self.assertFalse(services.execution_approval_free(task_id))
                services.hydrate(SimpleNamespace(projection={"application": view}))
                self.assertIs(expected, services.execution_approval_free(task_id))

    def test_a_paid_off_deployment_never_exercises_a_grant(self):
        application = self.application(
            dispatcher=False,
            langgraph_primary_offline_simulation_enabled=False,
            langgraph_primary_live_provider_dispatch_enabled=False,
        )
        self.assertFalse(self.exercise(application, self.record()))
        self.assertEqual([], self.audit())

    @staticmethod
    def coordinator(projection=None, *, ambiguous=0, unreadable=False, aggregate_unreadable=False,
                    failed_aggregate=False):
        def get_projection(task_id):
            if unreadable:
                raise OSError("projection unavailable")
            return None if projection is None else SimpleNamespace(projection=projection)

        def parallel_aggregate(task_id):
            if aggregate_unreadable:
                raise OSError("aggregate unavailable")
            return SimpleNamespace(ambiguous_slot_count=ambiguous,
                                   terminal=failed_aggregate, succeeded=False)

        return SimpleNamespace(get_projection=get_projection,
                               parallel_aggregate=parallel_aggregate)

    def test_trips_are_derived_from_durable_task_state_on_every_path(self):
        """Callback-settled, ambiguous and crash-after-terminal tasks all trip."""

        tripping = {
            # Settled by a provider callback, or its process stopped after the
            # terminal was persisted: either way only the durable state remains.
            "failed_terminal": self.coordinator({"terminal_classification": "FAILED"}),
            "owner_decision": self.coordinator(
                {"terminal_classification": "OWNER_DECISION_REQUIRED"}),
            "ambiguous_slot": self.coordinator(RUNNING, ambiguous=1),
            # Its aggregate ended failed, but the graph was never resumed.
            "failed_aggregate_unclassified": self.coordinator(RUNNING, failed_aggregate=True),
            "unreadable": self.coordinator(unreadable=True),
            "aggregate_unreadable": self.coordinator(RUNNING, aggregate_unreadable=True),
        }
        for name, state in tripping.items():
            with self.subTest(case=name):
                ledger = audit_ledger(self.directory)
                first = self.application(ledger)
                first.coordinator = state
                self.assertTrue(self.exercise(first, self.record()))
                earlier = self.audit(ledger)[0].acting_agent
                # A fresh process: nothing in memory carries the failure.
                second = self.application(ledger)
                second.coordinator = state
                self.assertFalse(self.exercise(second, self.record()))
                pre, trip, refused = self.audit(ledger)
                self.assertIs(RecordedOutcome.PRE_AUTHORIZED, pre.outcome)
                self.assertEqual({"delegation_trip": GateStatus.FAIL}, gate_statuses(trip))
                self.assertEqual(earlier, trip.acting_agent)
                self.assertIs(GateStatus.FAIL, gate_statuses(refused)["authorization"])
                self.assertFalse(second._delegation_tripped)
                # The trip is durable: a healthy state no longer re-arms it.
                third = self.application(ledger)
                third.coordinator = self.coordinator({"terminal_classification": "SUCCEEDED"})
                self.assertFalse(self.exercise(third, self.record()))
                self.assertEqual(1, sum(
                    "delegation_trip" in gate_statuses(item) for item in self.audit(ledger)))

    def test_succeeded_running_or_never_started_tasks_do_not_trip(self):
        for name, state in {
            "succeeded": self.coordinator({"terminal_classification": "SUCCEEDED"}),
            "running": self.coordinator(RUNNING),
            # Created, its parallel plan not registered yet: nothing dispatched.
            "registering": self.coordinator(
                {"terminal_classification": None}, aggregate_unreadable=True),
            # Its intake failed after the audit record: it spent an exercise
            # and ran nothing.
            "never_started": self.coordinator(None),
        }.items():
            with self.subTest(case=name):
                ledger = audit_ledger(self.directory)
                application = self.application(ledger)
                application.coordinator = state
                self.assertTrue(self.exercise(application, self.record()))
                self.assertTrue(self.exercise(application, self.record()))
                self.assertEqual(
                    [RecordedOutcome.PRE_AUTHORIZED] * 2,
                    [item.outcome for item in self.audit(ledger)])

    def test_a_concurrent_writer_cannot_overspend_the_last_exercise(self):
        """Count-then-append is conditional on the history that was counted."""

        grant = small_grant(max_exercises=1)
        path, anchor = str(Path(self.directory) / "shared.sqlite3"), _MemoryAuditAnchor()
        mine = TaskStore(path, delegation_audit_anchor=anchor)
        theirs = TaskStore(path, delegation_audit_anchor=anchor)
        other = self.application(theirs, delegation_grant_json=grant)
        other.coordinator = self.coordinator(None)
        read = mine.delegation_audit_records
        raced = []

        def read_then_race(**kwargs):
            page = read(**kwargs)
            if not raced:
                raced.append(True)
                # Another process spends the last exercise after this one counted.
                self.assertTrue(self.exercise(other, self.record()))
            return page

        application = self.application(mine, delegation_grant_json=grant)
        application.coordinator = self.coordinator(None)
        with patch.object(mine, "delegation_audit_records", side_effect=read_then_race):
            self.assertFalse(self.exercise(application, self.record()))
        self.assertEqual([RecordedOutcome.PRE_AUTHORIZED],
                         [item.outcome for item in theirs.delegation_audit_records()])
        # A lost race refuses this one intake; it does not latch the process.
        self.assertFalse(application._delegation_tripped)

    def test_main_intake_schedules_recover_only_via_callback(self):
        import importlib
        from fastapi import BackgroundTasks

        main = importlib.import_module("director_orchestrator.main")
        seen = {}

        class FakeApplication:
            def create_intake(self, payload, *, intake_authentication,
                              authenticated_replay_identity, on_dispatch_ready):
                seen["authentication"] = intake_authentication
                on_dispatch_ready("DIR-LG-1")
                return "created"

            def recover(self, task_id):
                raise AssertionError("recover runs only as a scheduled background task")

        application = FakeApplication()
        background = BackgroundTasks()
        payload = SlackIntake(
            source_event_id="event-main", channel_id="CDIRECTOR",
            thread_ts="1720000000.000001", user_id="UOWNER", text=SMALL_TEXT,
        )
        with patch.object(main, "primary_default_activation",
                          SimpleNamespace(application=application)), \
             patch.object(main, "settings", replace(
                 main.settings, langgraph_primary_new_intake_enabled=True,
                 allow_paid_runs=False)):
            self.assertEqual("created", main.intake(
                payload, background, intake_issued_at=None, intake_nonce=None,
                intake_signature=None))
        self.assertIs(IntakeAuthentication.BEARER_DRY_RUN, seen["authentication"])
        [task] = background.tasks
        self.assertEqual(application.recover, task.func)
        self.assertEqual(("DIR-LG-1",), task.args)


class ProviderAttemptRosterTests(unittest.TestCase):
    def test_success_stops_without_exercising_remaining_roster_slots(self) -> None:
        roster = (("json", "api"), ("text", "cli"))
        invoked: list[tuple[str, str]] = []
        settled: list[tuple[int, ParallelAssignmentStatus]] = []

        def reserve(ordinal: int) -> ProviderAttemptReservation:
            format_id, transport_id = roster[(ordinal - 1) % len(roster)]
            return ProviderAttemptReservation(
                "task", "assignment", ordinal, "openai", format_id,
                transport_id, digest(f"key-{ordinal}"), digest(f"attempt-{ordinal}"),
                digest("budget"),
            )

        status, result, _result_digest, reason = _execute_three_attempt_roster(
            format_roster=roster,
            reserve_attempt=reserve,
            record_attempt=lambda attempt, attempt_status, *_args: settled.append(
                (attempt.attempt_ordinal, attempt_status)
            ),
            dispatch_attempt=lambda attempt: (
                invoked.append((attempt.format_id, attempt.transport_id))
                or {"status": "SUCCEEDED", "attempt": attempt.attempt_ordinal}
            ),
        )

        self.assertEqual([roster[0]], invoked)
        self.assertEqual([(1, ParallelAssignmentStatus.SUCCEEDED)], settled)
        self.assertEqual(ParallelAssignmentStatus.SUCCEEDED, status)
        assert result is not None
        self.assertEqual(1, result["attempt"])
        self.assertEqual("provider_result_authenticated", reason)



    def test_one_format_success_invokes_only_the_primary_attempt(self) -> None:
        invoked: list[int] = []

        status, result, _result_digest, reason = _execute_three_attempt_roster(
            format_roster=(("json", "api"),),
            reserve_attempt=lambda ordinal: ProviderAttemptReservation(
                "task", "assignment", ordinal, "openai", "json", "api",
                digest(f"key-{ordinal}"), digest(f"attempt-{ordinal}"), digest("budget"),
            ),
            record_attempt=lambda *_args: None,
            dispatch_attempt=lambda attempt: (
                invoked.append(attempt.attempt_ordinal)
                or {"status": "SUCCEEDED", "attempt": attempt.attempt_ordinal}
            ),
        )

        self.assertEqual([1], invoked)
        self.assertEqual(ParallelAssignmentStatus.SUCCEEDED, status)
        assert result is not None
        self.assertEqual(1, result["attempt"])
        self.assertEqual("provider_result_authenticated", reason)

    def test_terminal_failure_does_not_retry_without_transient_evidence(self) -> None:
        invoked: list[int] = []
        settled: list[tuple[int, ParallelAssignmentStatus]] = []

        status, result, _result_digest, reason = _execute_three_attempt_roster(
            format_roster=(("json", "api"), ("text", "cli")),
            reserve_attempt=lambda ordinal: ProviderAttemptReservation(
                "task", "assignment", ordinal, "openai",
                *(("json", "api") if ordinal == 1 else ("text", "cli")),
                digest(f"key-{ordinal}"), digest(f"attempt-{ordinal}"), digest("budget"),
            ),
            record_attempt=lambda attempt, attempt_status, *_args: settled.append(
                (attempt.attempt_ordinal, attempt_status)
            ),
            dispatch_attempt=lambda attempt: (
                invoked.append(attempt.attempt_ordinal)
                or {"status": "FAILED", "attempt": attempt.attempt_ordinal}
            ),
        )

        self.assertEqual([1], invoked)
        self.assertEqual([(1, ParallelAssignmentStatus.FAILED)], settled)
        self.assertEqual(ParallelAssignmentStatus.FAILED, status)
        self.assertIsNone(result)
        self.assertEqual(PROVIDER_RETRY_REQUIRES_TRANSIENT_FAILURE, reason)

    def test_transient_recovery_uses_the_next_configured_transport_once(self) -> None:
        roster = (("json", "responses-api"), ("text", "native-cli"))
        invoked: list[tuple[str, str]] = []
        settled: list[tuple[int, ParallelAssignmentStatus, str]] = []

        def dispatch(attempt: ProviderAttemptReservation) -> dict[str, object]:
            invoked.append((attempt.format_id, attempt.transport_id))
            if attempt.attempt_ordinal == 1:
                raise ProviderTransientDispatchError("temporary transport outage")
            return {"status": "SUCCEEDED", "attempt": attempt.attempt_ordinal}

        status, result, _result_digest, reason = _execute_three_attempt_roster(
            format_roster=roster,
            reserve_attempt=lambda ordinal: ProviderAttemptReservation(
                "task", "assignment", ordinal, "openai",
                *roster[(ordinal - 1) % len(roster)],
                digest(f"key-{ordinal}"), digest(f"attempt-{ordinal}"), digest("budget"),
            ),
            record_attempt=lambda attempt, attempt_status, _digest, reason_code: settled.append(
                (attempt.attempt_ordinal, attempt_status, reason_code)
            ),
            dispatch_attempt=dispatch,
        )

        self.assertEqual([roster[0], roster[1]], invoked)
        self.assertEqual(
            [
                (1, ParallelAssignmentStatus.FAILED, PROVIDER_TRANSIENT_TRANSPORT_FAILURE),
                (2, ParallelAssignmentStatus.SUCCEEDED, "provider_result_authenticated"),
            ],
            settled,
        )
        self.assertEqual(ParallelAssignmentStatus.SUCCEEDED, status)
        assert result is not None
        self.assertEqual(2, result["attempt"])
        self.assertEqual("provider_result_authenticated", reason)

    def test_third_attempt_requires_a_different_transport_on_single_format_roster(
        self,
    ) -> None:
        invoked: list[int] = []

        def dispatch(attempt: ProviderAttemptReservation) -> dict[str, object]:
            invoked.append(attempt.attempt_ordinal)
            raise ProviderTransientDispatchError("temporary transport outage")

        status, result, _result_digest, reason = _execute_three_attempt_roster(
            format_roster=(("json", "api"),),
            reserve_attempt=lambda ordinal: ProviderAttemptReservation(
                "task", "assignment", ordinal, "openai", "json", "api",
                digest(f"key-{ordinal}"), digest(f"attempt-{ordinal}"), digest("budget"),
            ),
            record_attempt=lambda *_args: None,
            dispatch_attempt=dispatch,
        )

        self.assertEqual([1, 2], invoked)
        self.assertEqual(ParallelAssignmentStatus.FAILED, status)
        self.assertIsNone(result)
        self.assertEqual("provider_retry_evidence_insufficient", reason)

    def test_three_transient_failures_exhaust_the_route(self) -> None:
        roster = (("json", "api"), ("text", "cli"))
        invoked: list[int] = []

        def dispatch(attempt: ProviderAttemptReservation) -> dict[str, object]:
            invoked.append(attempt.attempt_ordinal)
            raise ProviderTransientDispatchError("temporary transport outage")

        status, result, _result_digest, reason = _execute_three_attempt_roster(
            format_roster=roster,
            reserve_attempt=lambda ordinal: ProviderAttemptReservation(
                "task", "assignment", ordinal, "openai",
                *roster[(ordinal - 1) % len(roster)],
                digest(f"key-{ordinal}"), digest(f"attempt-{ordinal}"), digest("budget"),
            ),
            record_attempt=lambda *_args: None,
            dispatch_attempt=dispatch,
        )

        self.assertEqual([1, 2, 3], invoked)
        self.assertEqual(ParallelAssignmentStatus.FAILED, status)
        self.assertIsNone(result)
        self.assertEqual(PROVIDER_ATTEMPT_BUDGET_EXHAUSTED, reason)

    def test_ambiguous_attempt_consumes_one_slot_and_never_redispatches(self) -> None:
        invoked: list[int] = []
        settled: list[tuple[int, ParallelAssignmentStatus]] = []

        def reserve(ordinal: int) -> ProviderAttemptReservation:
            return ProviderAttemptReservation(
                "task", "assignment", ordinal, "openai", "json", "api",
                digest(f"key-{ordinal}"), digest(f"attempt-{ordinal}"), digest("budget"),
            )

        def dispatch(attempt: ProviderAttemptReservation) -> dict[str, object]:
            invoked.append(attempt.attempt_ordinal)
            raise RuntimeError("outcome unknown")

        status, result, result_digest, reason = _execute_three_attempt_roster(
            format_roster=(("json", "api"),),
            reserve_attempt=reserve,
            record_attempt=lambda attempt, attempt_status, *_args: settled.append(
                (attempt.attempt_ordinal, attempt_status)
            ),
            dispatch_attempt=dispatch,
        )

        self.assertEqual([1], invoked)
        self.assertEqual([(1, ParallelAssignmentStatus.AMBIGUOUS)], settled)
        self.assertEqual(ParallelAssignmentStatus.AMBIGUOUS, status)
        self.assertIsNone(result)
        self.assertIsNone(result_digest)
        self.assertEqual("provider_outcome_unknown", reason)

    def test_later_ambiguous_attempt_does_not_run_after_authenticated_success(
        self,
    ) -> None:
        invoked: list[int] = []
        settled: list[tuple[int, ParallelAssignmentStatus]] = []

        status, result, result_digest, reason = _execute_three_attempt_roster(
            format_roster=(("json", "api"),),
            reserve_attempt=lambda ordinal: ProviderAttemptReservation(
                "task", "assignment", ordinal, "openai", "json", "api",
                digest(f"key-{ordinal}"), digest(f"attempt-{ordinal}"), digest("budget"),
            ),
            record_attempt=lambda attempt, attempt_status, *_args: settled.append(
                (attempt.attempt_ordinal, attempt_status)
            ),
            dispatch_attempt=lambda attempt: (
                invoked.append(attempt.attempt_ordinal)
                or {"status": "SUCCEEDED", "attempt": 1}
            ),
        )

        self.assertEqual([1], invoked)
        self.assertEqual([(1, ParallelAssignmentStatus.SUCCEEDED)], settled)
        self.assertEqual(ParallelAssignmentStatus.SUCCEEDED, status)
        assert result is not None
        self.assertEqual(1, result["attempt"])
        self.assertEqual(document_digest(result), result_digest)
        self.assertEqual("provider_result_authenticated", reason)


class DefaultPrimaryLiveStartupBoundaryTests(unittest.TestCase):
    def test_live_activation_composes_a_ready_typed_effect_executor(self):
        class Gateway:
            def __init__(self, system, action):
                self.system = system
                self.action = action

            def mutate(self, intent, timeout_seconds):  # pragma: no cover
                raise AssertionError("composition test must not call a gateway")

            def readback(self, intent, remote_identifier, timeout_seconds):  # pragma: no cover
                raise AssertionError("composition test must not call a gateway")

        registry = ExactEffectGatewayRegistry(
            (
                Gateway("slack", SLACK_POST_MESSAGE),
                Gateway("github", GITHUB_MARK_READY),
            ),
            allowed_actions=frozenset(
                {("slack", SLACK_POST_MESSAGE), ("github", GITHUB_MARK_READY)}
            ),
        )
        settings = replace(
            Settings(),
            langgraph_primary_enabled=True,
            langgraph_primary_live_provider_dispatch_enabled=True,
            allow_paid_runs=True,
            langgraph_primary_owner_token="o" * 32,
            repository_allowlist=("owner/repository",),
            allowed_starting_refs=("main",),
            repository_snapshot_root=str(Path.cwd()),
        )
        handle = SimpleNamespace(
            claim_store=SimpleNamespace(), pool=SimpleNamespace(), close=lambda: None,
            schema="test_budget_v1",
        )
        from director_orchestrator.antigravity_team import PrimaryProviderBudgetAuthority
        from director_orchestrator.antigravity_team_routing import PrimaryProviderBudgetRouting
        dependencies = SimpleNamespace(
            team_routing=None,
            budget_routing=PrimaryProviderBudgetRouting(
                authority=PrimaryProviderBudgetAuthority(object(), primary_schema=handle.schema), pool=handle.pool),
            validate=lambda _required: None,
            adapters={provider: PacketBoundaryAdapter(
                    provider, SimpleNamespace(dispatch=lambda *_args, **_kwargs: {}))
                for provider in ("openai", "claude", "cursor")},
            packet_result_resolver=lambda _context: None,
            completion_evidence_resolver=lambda _context: None,
            external_effect_resolver=lambda _context: None,
            evidence_debate_handler=lambda _context: None,
            acceptance_handler=lambda _context: None,
            candidate_sha_resolver=lambda *_args: SHA,
            format_roster=lambda _provider: (("api", "api"),),
            adapter_for_format=lambda *_args: None,
        )
        provider_activation = SimpleNamespace(
            health=lambda: {"combined_live_ready": True, "core_live_ready": True},
            provider_ready=lambda *_args, **_kwargs: True,
        )
        captured = {}

        class Coordinator:
            def __init__(self, *_args, **kwargs):
                captured.update(kwargs)

        with (
            patch(
                "director_orchestrator.primary_default.resolve_default_primary_authority",
                return_value=DefaultPrimaryAuthority(SHA, digest("authority")),
            ),
            patch(
                "director_orchestrator.primary_default.open_postgres_control_plane",
                return_value=handle,
            ),
            patch.object(Settings, "primary_provider_activation_bindings", return_value={"openai": object()}),
            patch(
                "director_orchestrator.primary_default.PrimaryProviderActivationService",
                return_value=provider_activation,
            ),
            patch(
                "director_orchestrator.primary_default.PrimaryTaskCoordinator",
                Coordinator,
            ),
        ):
            activation = DefaultPrimaryActivation(
                settings,
                live_dependencies=dependencies,
                effect_gateways=registry,
            )
            # Separately sufficient pools cannot claim readiness when their
            # intersection lacks a second independent specialist.
            provider_activation.provider_ready = lambda provider, **_kwargs: provider in {"openai", "claude", "grok"}
            with self.assertRaisesRegex(ValueError, "mandatory core"):
                DefaultPrimaryActivation(settings, live_dependencies=dependencies,
                                         effect_gateways=registry)

        self.assertTrue(captured["effect_executor"].ready)
        self.assertIsNotNone(activation.application)

        # Exercise the constructed live dispatch closure: its authority is the
        # admitted attempt, not an optional field or model label in a packet.
        record = TaskRecord(task_id="DIR-TOKEN-BOUND", status=TaskStatus.QUEUED,
            intake=SlackIntake(source_event_id="token-bound", channel_id="C123",
                              user_id="U123", text="Review a bounded change", thread_ts="1.0"))
        plan = preview_plan(record.intake.text, settings)
        assignment = plan.assignments[0].model_copy(update={"agent": ProviderName.OPENAI,
            "primary_provider_lane": "openai", "estimated_cost_usd": 1.0})
        record.plan = plan.model_copy(update={"assignments": [assignment]})
        activation.services._packet_router = packet_router(settings)
        activation.services.register(record)
        assignment_id = activation.services._packet_router._assignment_id(record, assignment)
        reservation = ParallelReservation(record.task_id, assignment_id, assignment.sequence,
                                          "openai", "test-deployment", digest("reservation"))
        writer = SimpleNamespace(engine_owner="langgraph", owner_token=settings.langgraph_primary_owner_token,
                                 lease_live=True, fencing_generation=4)
        # Dispatch reads the writer, persists the artifact and re-reads the
        # wave's cancellation row inside the bound its own readiness
        # established, so each double records the bound it was handed.
        from contextlib import contextmanager
        bounds = []

        class BatchRow:
            """The existing `director_primary_parallel_batch` kill switch.

            Answers the same statements the real connection does: the
            transaction's recorded bound (none yet, on a fresh transaction)
            and the wave's kill switch.
            """

            info = SimpleNamespace(server_version=170000)

            def __enter__(self):
                return self

            def __exit__(self, *_exception):
                return False

            @contextmanager
            def transaction(self):
                # The bounded read path opens one explicit transaction, so the
                # LOCAL timeouts it sets still apply to the statement after.
                yield self

            def execute(self, statement, *_args, **_kwargs):
                row = ({"bound": None} if "director.transaction_deadline" in statement
                       else {"kill_switch": False})
                return SimpleNamespace(fetchone=lambda: row)

        def batch_connection(*_args, **kwargs):
            bounds.append(("cancellation", kwargs.get("timeout")))
            return BatchRow()

        handle.pool.connection = batch_connection
        handle.claim_store.current_task_writer = lambda _task, *, deadline=None: (
            bounds.append(("writer", deadline)) or writer)
        handle.claim_store.record_work_packet_artifact = lambda **kwargs: (
            bounds.append(("artifact", kwargs.get("deadline"))) or SimpleNamespace(
                document_digest=document_digest(kwargs["packet"])))
        attempt = ProviderAttemptReservation(record.task_id, assignment_id, 1, "openai", "api", "api",
                                             digest("key"), digest("attempt"), digest("budget"),
                                             total_token_limit=731)
        activation.coordinator.reserve_provider_attempt = lambda **_kwargs: attempt
        activation.coordinator.record_provider_attempt = lambda **_kwargs: None
        contexts = []
        dependencies.adapter_for_format = lambda *_args: SimpleNamespace(
            dispatch=lambda _packet, *, context: (contexts.append(context) or {"status": "FAILED"}))
        # Exercise the actual factory's pre-ACTIVE closure with a genuine signed
        # synthetic budget and the real packet (no fake preflight callback).
        from tests.test_antigravity_team import grant, budget, fixtures
        from datetime import datetime, timezone
        from decimal import Decimal, ROUND_CEILING
        from director_orchestrator.primary_paid_preflight import PlannedProviderAttempt
        from director_orchestrator.primary_parallel import PaidSetupHold
        from unittest.mock import Mock
        packet = activation.services._packet_router.planned_work_packet(record, assignment)
        signed_budget = budget(grant(), provider_id="openai", project_id=packet["project"],
            task_id=record.task_id,
            task_limit_microusd=int(Decimal(str(settings.max_task_budget_usd))*1000000),
            daily_limit_microusd=int(Decimal(str(settings.max_daily_provider_budget_usd))*1000000),
            attempt_reservation_microusd=int((Decimal(str(float(packet["limits"]["budget_usd"])/3))*1000000).to_integral_value(rounding=ROUND_CEILING)))
        dependencies.budget_routing.authority.entitlements = SimpleNamespace(
            _clock=lambda:datetime.now(timezone.utc), _owner_authority_secret=fixtures.OWNER_SECRET)
        def connection():
            result=Mock()
            result.execute.side_effect=[Mock(fetchall=lambda:[dict(authority_digest=signed_budget.grant_digest,
                document=signed_budget.model_dump(mode="json"))]),Mock(fetchone=lambda:None),
                Mock(fetchone=lambda:None),Mock(fetchone=lambda:dict(task_spend=0,daily_spend=0))]
            return result
        planned=[]
        selected_adapter = dependencies.adapter_for_format("openai",("api","api"))
        selected_adapter._transport=SimpleNamespace(response_meter=object(),
            preflight=lambda payload, *, context:planned.append(context))
        dependencies.adapter_for_format=lambda *_args:selected_adapter
        activation.application._parallel_dispatcher.preflight(reservation,writer=writer,
            connection=connection(),wave_budget_usage={})
        self.assertEqual(len(planned),1)
        self.assertIsInstance(planned[0],PlannedProviderAttempt)
        self.assertEqual(planned[0].total_token_limit,signed_budget.token_limit)
        self.assertEqual(contexts,[])
        absent=Mock(); absent.execute.return_value.fetchall.return_value=[]
        with self.assertRaises(PaidSetupHold):
            activation.application._parallel_dispatcher.preflight(reservation,writer=writer,
                connection=absent,wave_budget_usage={})
        self.assertEqual(contexts,[])
        with patch("director_orchestrator.primary_default.live_provider_admissions", return_value={}):
            outcome = activation.application._parallel_dispatcher(reservation)
        self.assertEqual(ParallelAssignmentStatus.FAILED, outcome.status)
        self.assertEqual(1, len(contexts))
        self.assertEqual(731, contexts[0].total_token_limit)
        self.assertEqual(attempt.attempt_digest, contexts[0].idempotency_key)
        self.assertEqual(4, contexts[0].fencing_generation)
        # The same readiness bound reaches the writer read, the artifact write,
        # the pre-I/O cancellation read and the provider call itself.
        self.assertEqual({"writer", "artifact", "cancellation"},
                          {stage for stage, _bound in bounds})
        self.assertTrue(all(bound is not None and bound > 0 for _stage, bound in bounds), bounds)
        self.assertIsNotNone(contexts[0].deadline)

    def test_exact_effect_gateway_action_binding_fails_closed_when_missing(self):
        gateway = SimpleNamespace(system="slack", action=SLACK_POST_MESSAGE)
        with self.assertRaisesRegex(ValueError, "exactly match"):
            ExactEffectGatewayRegistry(
                (gateway,),
                allowed_actions=frozenset(
                    {("slack", SLACK_POST_MESSAGE), ("github", GITHUB_MARK_READY)}
                ),
            )

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

    def test_reconciliation_readiness_is_not_sticky_after_failed_pass(self):
        activation = object.__new__(DefaultPrimaryActivation)
        activation.application = SimpleNamespace(
            recover=lambda _task_id: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        activation.services = SimpleNamespace(occupied=lambda _task_id: False)
        projection = SimpleNamespace(
            task_id="task-freshness",
            projection={"terminal_classification": None},
        )
        activation.coordinator = SimpleNamespace(
            list_projections=lambda: (projection,),
            get_task_writer=lambda _task_id: None,
        )
        activation._reconciliation_worker_ready = True

        with self.assertRaisesRegex(RuntimeError, "boom"):
            activation.reconcile_once()
        self.assertFalse(activation._reconciliation_worker_ready)

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

    def test_automatic_live_factory_requires_budget_enrollment_with_team_disabled(self):
        from director_orchestrator.antigravity_team import TeamAuthorizationError
        settings = replace(Settings(), langgraph_primary_antigravity_team_enabled=False,
            entitlement_control_plane_enabled=False)
        adapters = {provider: object() for provider in ("openai", "claude", "cursor")}
        with patch("director_orchestrator.primary_default.discover_local_primary_command_transports", return_value=({}, {})), \
             patch("director_orchestrator.primary_default.build_graph_native_packet_adapters", return_value=adapters), \
             patch("director_orchestrator.primary_default.build_primary_packet_result_resolver", return_value=lambda _:None):
            with self.assertRaisesRegex(TeamAuthorizationError, "enrolled daily provider budget"):
                build_automatic_primary_live_dependencies(settings, None, None)

    def test_automatic_live_factory_keeps_independent_typed_budget_resolver(self):
        from director_orchestrator.antigravity_team import PrimaryProviderBudgetAuthority
        from director_orchestrator.antigravity_team_routing import PrimaryProviderBudgetRouting
        settings = replace(Settings(), langgraph_primary_antigravity_team_enabled=False)
        routing = PrimaryProviderBudgetRouting(
            authority=PrimaryProviderBudgetAuthority(object(), primary_schema="test_budget_v1"), pool=object())
        providers = ("openai", "claude", "cursor")
        adapters = {provider: object() for provider in providers}
        capabilities = {provider: SimpleNamespace(transport_kind="test") for provider in providers}
        with patch("director_orchestrator.primary_default.discover_local_primary_command_transports", return_value=({}, capabilities)), \
             patch("director_orchestrator.primary_default.build_graph_native_packet_adapters", return_value=adapters), \
             patch("director_orchestrator.primary_default.build_primary_packet_result_resolver", return_value=lambda _:None), \
             patch("director_orchestrator.antigravity_team_routing.configured_provider_budget_routing", return_value=routing) as construct:
            dependencies = build_automatic_primary_live_dependencies(settings, None, None)
        construct.assert_called_once_with(settings, None)
        self.assertIs(dependencies.budget_routing, routing)
        self.assertIsNone(dependencies.team_routing)
        self.assertIs(dependencies.capabilities, capabilities)
        
        settings_bridge = replace(
            settings,
            langgraph_primary_host_bridge_endpoint="https://localhost/v1/host-provider/dispatch",
            langgraph_primary_host_bridge_secret="a" * 32
        )
        sentinel = object()
        with patch("director_orchestrator.primary_default.discover_local_primary_command_transports", return_value=({}, capabilities)), \
             patch("director_orchestrator.primary_default.build_graph_native_packet_adapters", return_value=adapters), \
             patch("director_orchestrator.primary_default.build_primary_packet_result_resolver", return_value=lambda _:None), \
             patch("director_orchestrator.antigravity_team_routing.configured_provider_budget_routing", return_value=routing), \
             patch("director_orchestrator.primary_default.challenge_host_provider_bridge", return_value=sentinel):
            dependencies_bridge = build_automatic_primary_live_dependencies(settings_bridge, None, None)
        self.assertIs(dependencies_bridge.bridge_challenge, sentinel)



class DefaultPrimaryLiveWaveBoundaryTests(unittest.TestCase):
    """Budget, packet identity and effect-target authority for the live wave."""

    def _record(self, settings: Settings, text: str = "Implement the bounded change"):
        intake = SlackIntake(
            source_event_id="live-wave-event",
            channel_id="C0BAV9CMC80",
            user_id="U123",
            text=text,
            thread_ts="1787450001.123456",
        )
        record = TaskRecord(
            task_id="DIR-LG-LIVEWAVE",
            status=TaskStatus.QUEUED,
            intake=intake,
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        record.plan = preview_plan(intake.text, settings)
        return record

    def test_validated_antigravity_cost_alias_routes_the_actual_lane(self):
        settings = replace(Settings(), repository_allowlist=("owner/repository",),
            trusted_provider_cost_ranks_json='{"openai":4,"claude":3,"cursor":2,"gemini":1}')
        record = self._record(settings)
        record.intake = record.intake.model_copy(update={"primary_required_capabilities":("bounded_scaffolding",)})
        _live_parallel_plan(record, settings, available=("openai","claude","cursor","antigravity"), history=())
        self.assertEqual(record.plan.assignments[0].primary_provider_lane,"antigravity")
        self.assertIn(record.plan.assignments[-1].primary_provider_lane, {"claude","cursor","grok"})

    def test_profiles_freeze_through_plan_and_prestage(self):
        from director_orchestrator.allocation_profiles import allocation_profile_digest
        settings = replace(Settings(), repository_allowlist=("owner/repository",),
            trusted_provider_cost_ranks_json='{"openai":1,"claude":2,"cursor":3,"grok":4}')
        for profile in ("engineering", "mixed", "public_research", "enterprise"):
            with self.subTest(profile=profile):
                record = self._record(settings)
                record.intake = record.intake.model_copy(update={"allocation_profile":profile})
                _live_parallel_plan(record, settings, available=("openai","claude","cursor","grok"), history=())
                self.assertEqual({item.allocation_profile for item in record.plan.assignments}, {profile})
                self.assertNotEqual(record.plan.assignments[0].agent, record.plan.assignments[-1].agent)
                if record.plan.prestage is not None:
                    self.assertEqual(record.plan.prestage.allocation_profile_digest, allocation_profile_digest(profile))

    def test_live_wave_binds_a_positive_bounded_per_lane_budget(self):
        settings = replace(
            Settings(),
            repository_allowlist=("owner/repository",),
            max_task_budget_usd=2.0,
        )
        record = self._record(settings)
        self.assertTrue(
            all(
                assignment.estimated_cost_usd == 0
                for assignment in record.plan.assignments
            )
        )

        _live_parallel_plan(record, replace(settings, trusted_provider_cost_ranks_json='{"openai":4,"claude":2,"cursor":1,"grok":3,"perplexity":5,"firecrawl":6}'), available=("openai", "claude", "cursor", "grok"), history=())

        lanes = ("cursor",)
        self.assertEqual(len(lanes) + 1, len(record.plan.assignments))
        expected = settings.max_task_budget_usd / (len(lanes) + 1)
        for assignment in record.plan.assignments:
            self.assertEqual(expected, assignment.estimated_cost_usd)
            self.assertGreater(assignment.estimated_cost_usd, 0)
            self.assertLessEqual(
                assignment.estimated_cost_usd, settings.max_task_budget_usd
            )
        self.assertAlmostEqual(
            settings.max_task_budget_usd,
            sum(item.estimated_cost_usd for item in record.plan.assignments),
        )
        # Every supported lane keeps its own slot.
        self.assertEqual(
            list(lanes),
            [
                item.primary_provider_lane
                for item in record.plan.assignments[: len(lanes)]
            ],
        )

    def test_research_is_explicit_public_only_and_precedes_implementation(self):
        settings = replace(Settings(), repository_allowlist=("owner/repository",),
            trusted_provider_cost_ranks_json='{"openai":4,"claude":2,"cursor":1,"grok":3,"perplexity":5,"firecrawl":6}')
        record = self._record(settings, text="PRIVATE-CONTEXT implementation")
        record.intake = record.intake.model_copy(update={
            "allocation_profile": "public_research",
            "primary_required_capabilities": ("public_research",),
            "primary_public_research_query": "public API specification",
        })
        _live_parallel_plan(record, settings, available=("openai", "claude", "cursor", "perplexity"), history=())
        research, producer, reviewer = record.plan.assignments
        self.assertEqual(research.task, "public API specification")
        self.assertEqual(research.public_research_query, research.task)
        self.assertIsNone(research.repository)
        self.assertEqual(producer.depends_on, [1])
        self.assertNotEqual(producer.primary_provider_lane, reviewer.primary_provider_lane)
        self.assertNotIn("PRIVATE-CONTEXT", research.model_dump_json())
        packet = packet_router(settings).planned_work_packet(record, research)
        self.assertNotIn("PRIVATE-CONTEXT", json.dumps(packet))
        self.assertEqual(packet["objective"], "public API specification")

    def test_zero_configured_task_budget_fails_the_live_wave_closed(self):
        settings = replace(
            Settings(),
            repository_allowlist=("owner/repository",),
            max_task_budget_usd=0.0,
        )
        record = self._record(settings)

        with self.assertRaisesRegex(RuntimeError, "positive configured task budget"):
            _live_parallel_plan(record, replace(settings, trusted_provider_cost_ranks_json='{"openai":4,"claude":2,"cursor":1,"grok":3,"perplexity":5,"firecrawl":6}'), available=("openai", "claude", "cursor", "grok"), history=())

    def test_terminal_review_barrier_is_identified_structurally(self):
        settings = replace(
            Settings(),
            repository_allowlist=("owner/repository",),
            max_task_budget_usd=2.0,
        )
        record = self._record(settings)
        _live_parallel_plan(record, replace(settings, trusted_provider_cost_ranks_json='{"openai":4,"claude":2,"cursor":1,"grok":3,"perplexity":5,"firecrawl":6}'), available=("openai", "claude", "cursor", "grok"), history=())
        assignments = record.plan.assignments
        producer = min(assignments, key=lambda item: item.sequence)
        review = max(assignments, key=lambda item: item.sequence)

        bound = _terminal_review_assignment(record)

        self.assertIs(bound, review)
        self.assertIsNot(bound, producer)
        # The reviewer deliberately reuses a first-wave lane, so lane equality
        # could never have selected the barrier.
        reused = [
            item
            for item in assignments
            if _assignment_provider_lane(item) == _assignment_provider_lane(review)
        ]
        self.assertEqual(len(reused), 1)
        self.assertNotEqual(_assignment_provider_lane(producer), _assignment_provider_lane(review))

        detached = record.model_copy(
            update={
                "plan": record.plan.model_copy(
                    update={
                        "assignments": [
                            *assignments[:-1],
                            review.model_copy(update={"depends_on": []}),
                        ]
                    }
                )
            }
        )
        self.assertIsNone(_terminal_review_assignment(detached))

    def _effect_context(self, record, candidate="a" * 40, starting="a" * 40):
        return SimpleNamespace(
            task_id=record.task_id,
            candidate_sha=candidate,
            starting_sha=starting,
        )

    def _grant_settings(self, pull_number=82):
        return replace(
            Settings(),
            repository_allowlist=("owner/repository",),
            langgraph_primary_external_effect_pull_number=pull_number,
            langgraph_primary_external_effect_base_sha="b" * 40,
            langgraph_primary_external_effect_head_sha="c" * 40,
        )

    def test_github_effect_is_pinned_to_configured_director_authority(self):
        settings = self._grant_settings()
        record = self._record(settings)
        grant = build_typed_external_effect_grant(
            settings, DefaultPrimaryAuthority("a" * 40, digest("authority"))
        )
        assert grant is not None

        system, action, target, payload = _typed_effect_binding(
            grant=grant,
            record=record,
            context=self._effect_context(record),  # type: ignore[arg-type]
            granted_kinds=frozenset({"github_mark_ready"}),
            effect={
                "kind": "github_mark_ready",
                "target": "owner/repository#82",
                "status": "NOT_ATTEMPTED",
                "idempotency_key": "proposal-1",
            },
        )

        self.assertEqual(("github", GITHUB_MARK_READY), (system, action))
        self.assertEqual(
            {
                "repository": "owner/repository",
                "pull_number": 82,
                "head_sha": "c" * 40,
                "base_sha": "b" * 40,
            },
            target,
        )
        self.assertEqual({}, payload)
        self.assertEqual("owner/repository#82", grant.proposal_target)
        self.assertEqual(
            ("github_mark_ready",), typed_external_effect_grant_kinds(settings)
        )

    def test_github_effect_fails_closed_without_configured_pull_authority(self):
        record = self._record(self._grant_settings())
        authority = DefaultPrimaryAuthority("a" * 40, digest("authority"))
        unconfigured = self._grant_settings(pull_number=0)
        multi_repository = replace(
            self._grant_settings(),
            repository_allowlist=("owner/repository", "owner/second"),
        )

        for settings in (unconfigured, multi_repository,
                         replace(self._grant_settings(), langgraph_primary_external_effect_base_sha=""),
                         replace(self._grant_settings(), langgraph_primary_external_effect_head_sha="invalid")):
            with self.subTest(repositories=settings.repository_allowlist):
                self.assertEqual((), typed_external_effect_grant_kinds(settings))
                self.assertIsNone(
                    build_typed_external_effect_grant(settings, authority)
                )
                with self.assertRaisesRegex(
                    RuntimeError, "configured positive pull request"
                ):
                    _typed_effect_binding(
                        grant=None,
                        record=record,
                        context=self._effect_context(record),  # type: ignore[arg-type]
                        granted_kinds=frozenset({"github_mark_ready"}),
                        effect={
                            "kind": "github_mark_ready",
                            "target": "owner/repository#82",
                            "status": "NOT_ATTEMPTED",
                            "idempotency_key": "missing-authority",
                        },
                    )

    def test_ungranted_effect_kind_never_reaches_a_gateway(self):
        settings = self._grant_settings()
        record = self._record(settings)
        grant = build_typed_external_effect_grant(
            settings, DefaultPrimaryAuthority("a" * 40, digest("authority"))
        )

        # The work packet, not a closed vocabulary, is the only grant authority.
        with self.assertRaisesRegex(RuntimeError, "not granted by the work packet"):
            _typed_effect_binding(
                grant=grant,
                record=record,
                context=self._effect_context(record),  # type: ignore[arg-type]
                granted_kinds=frozenset(),
                effect={
                    "kind": "github_mark_ready",
                    "target": "owner/repository#82",
                    "status": "NOT_ATTEMPTED",
                    "idempotency_key": "ungranted",
                },
            )

    def test_effect_grant_is_digested_into_owner_approval_identity(self):
        def effect_key_digest(pull_number: int) -> str:
            settings = self._grant_settings(pull_number=pull_number)
            application = DefaultPrimaryApplication(
                settings,
                coordinator=None,  # type: ignore[arg-type]
                services=None,  # type: ignore[arg-type]
                authority=DefaultPrimaryAuthority("a" * 40, digest("authority")),
            )
            intake = SlackIntake(
                source_event_id="effect-grant-event",
                channel_id="C0BAV9CMC80",
                user_id="U123",
                text="Implement the bounded change and prepare a draft PR",
                thread_ts="1787450001.123456",
            )
            record = TaskRecord(
                task_id="DIR-LG-GRANT",
                status=TaskStatus.QUEUED,
                intake=intake,
                intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
            )
            plan = preview_plan(intake.text, settings)
            assignments = list(plan.assignments)
            assignments[0] = assignments[0].model_copy(update={"allow_draft_pr": True})
            record.plan = plan.model_copy(update={"assignments": assignments})
            request = application._request(record)
            self.assertTrue(request.external_effect_required)
            assert request.effect_key_digest is not None
            return request.effect_key_digest

        configured = effect_key_digest(82)
        self.assertEqual(configured, effect_key_digest(82))
        # A different approved pull request is a different approval identity.
        self.assertNotEqual(configured, effect_key_digest(83))
        self.assertNotEqual(configured, effect_key_digest(0))

    def test_redirected_repository_pull_or_revision_fails_closed(self):
        settings = self._grant_settings()
        record = self._record(settings)
        grant = build_typed_external_effect_grant(
            settings, DefaultPrimaryAuthority("a" * 40, digest("authority"))
        )
        assert grant is not None

        def bind(effect, context=None):
            return _typed_effect_binding(
                grant=grant,
                record=record,
                context=context or self._effect_context(record),  # type: ignore[arg-type]
                granted_kinds=frozenset({"github_mark_ready", "slack_post"}),
                effect=effect,
            )

        def proposal(target, key):
            return {
                "kind": "github_mark_ready",
                "target": target,
                "status": "NOT_ATTEMPTED",
                "idempotency_key": key,
            }

        with self.assertRaisesRegex(RuntimeError, "outside the repository allowlist"):
            bind(proposal("attacker/fork#82", "redirected-repository"))
        with self.assertRaisesRegex(RuntimeError, "not the configured pull request"):
            bind(proposal("owner/repository#83", "redirected-pull-request"))
        # Deployment/task source identity cannot rewrite the independently
        # approved PR target. The remote gateway revalidates these PR revisions.
        result = bind(proposal("owner/repository#82", "independent-pr"),
                      context=self._effect_context(record, candidate="d" * 40))
        self.assertEqual(result[2]["head_sha"], "c" * 40)
        self.assertEqual(result[2]["base_sha"], "b" * 40)
        with self.assertRaisesRegex(RuntimeError, "Slack effect target changed"):
            bind(
                {
                    "kind": "slack_post",
                    "target": "C0000000000",
                    "status": "NOT_ATTEMPTED",
                    "idempotency_key": "redirected-channel",
                }
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

        self.git(
            "remote",
            "set-url",
            "origin",
            "https://example.invalid/github.com/owner/pinned.git",
        )
        try:
            with self.assertRaisesRegex(RuntimeError, "remote does not match"):
                resolve_default_primary_authority(self.settings())
        finally:
            self.git(
                "remote",
                "set-url",
                "origin",
                "https://github.com/owner/pinned.git",
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


class DefaultPrimarySplitProjectAuthorityTests(unittest.TestCase):
    @staticmethod
    def _init_repo(path: Path, remote: str) -> None:
        subprocess.run(("git", "init", "-b", "main"), cwd=path, check=True, capture_output=True)
        subprocess.run(
            ("git", "config", "user.email", "split-authority@example.test"),
            cwd=path,
            check=True,
        )
        subprocess.run(
            ("git", "config", "user.name", "Split Authority Test"),
            cwd=path,
            check=True,
        )
        (path / "README.md").write_text(f"{remote}\n", encoding="utf-8")
        subprocess.run(("git", "add", "README.md"), cwd=path, check=True)
        subprocess.run(("git", "commit", "-m", "init"), cwd=path, check=True)
        subprocess.run(
            ("git", "remote", "add", "origin", f"https://github.com/{remote}.git"),
            cwd=path,
            check=True,
        )

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        base = Path(self.temporary_directory.name)
        self.director_root = base / "director"
        self.project_root = base / "project"
        self.director_root.mkdir()
        self.project_root.mkdir()
        self._init_repo(self.director_root, "owner/director")
        self._init_repo(self.project_root, "owner/edge-project")
        (self.project_root / "edge.txt").write_text("edge\n", encoding="utf-8")
        subprocess.run(("git", "add", "edge.txt"), cwd=self.project_root, check=True)
        subprocess.run(("git", "commit", "-m", "edge"), cwd=self.project_root, check=True)
        self.director_commit = subprocess.check_output(
            ("git", "rev-parse", "HEAD"), cwd=self.director_root, text=True
        ).strip()
        self.director_tree = subprocess.check_output(
            ("git", "rev-parse", "HEAD^{tree}"), cwd=self.director_root, text=True
        ).strip()
        self.project_commit = subprocess.check_output(
            ("git", "rev-parse", "HEAD"), cwd=self.project_root, text=True
        ).strip()
        self.project_tree = subprocess.check_output(
            ("git", "rev-parse", "HEAD^{tree}"), cwd=self.project_root, text=True
        ).strip()

    def split_settings(self, **updates) -> Settings:
        values = {
            "repository_snapshot_root": str(self.project_root),
            "repository_allowlist": ("owner/edge-project",),
            "allowed_starting_refs": ("main",),
            "langgraph_primary_pinned_commit_sha": self.director_commit,
            "langgraph_primary_pinned_tree_sha": self.director_tree,
            "langgraph_primary_project_execution_pinned_commit_sha": self.project_commit,
            "langgraph_primary_project_execution_pinned_tree_sha": self.project_tree,
        }
        values.update(updates)
        return replace(Settings(), **values)

    def test_split_project_execution_authority_accepts_edge_snapshot(self) -> None:
        authority = resolve_default_primary_authority(self.split_settings())
        self.assertEqual(self.project_commit, authority.starting_sha)

    def test_split_intake_plan_reaches_packet_router_with_project_pins(self) -> None:
        from director_orchestrator.policy import execution_policy_errors

        settings = self.split_settings()
        for preplanned in (True, False):
            with self.subTest(preplanned=preplanned):
                services = DefaultDryRunServices(settings)
                record = TaskRecord(
                    task_id="DIR-SPLIT-PLAN",
                    status=TaskStatus.QUEUED,
                    intake=SlackIntake(
                        source_event_id="event-split-plan",
                        channel_id="CSPLIT",
                        thread_ts="1720000000.000003",
                        user_id="UOWNER",
                        text="Project: default\nImplement the pinned project change.",
                    ),
                    intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
                )
                if preplanned:
                    record.plan = preview_plan(record.intake.text, settings)
                services.register(record)
                context = HandlerContext(
                    task_id=record.task_id,
                    source_event_id=record.intake.source_event_id,
                    correlation_id="corr-split-plan",
                    intake_digest=digest("intake"),
                    scope_digest=digest("scope"),
                    authority_snapshot_digest=digest("authority"),
                    work_packet_digest=None,
                    idempotency_key_digest=digest("idempotency"),
                    starting_sha=self.project_commit,
                    candidate_sha=self.project_commit,
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
                self.assertIsNotNone(planned)
                self.assertIsNotNone(planned.prestage)
                self.assertEqual(self.project_commit, planned.prestage.base_sha)
                self.assertEqual(self.project_commit, planned.prestage.branch)
                self.assertTrue(all(
                    item.starting_ref == self.project_commit for item in planned.assignments
                ))
                self.assertFalse([
                    error for error in execution_policy_errors(planned, settings)
                    if "digest" in error
                ])

    def test_service_pins_without_project_execution_reject_foreign_snapshot(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "HEAD and starting ref"):
            resolve_default_primary_authority(
                self.split_settings(
                    langgraph_primary_project_execution_pinned_commit_sha="",
                    langgraph_primary_project_execution_pinned_tree_sha="",
                )
            )

    def test_split_authority_preserves_repository_tree_and_clean_checks(self) -> None:
        for updates, message in (
            ({"repository_allowlist": ("other/project",)}, "remote does not match"),
            ({"langgraph_primary_project_execution_pinned_tree_sha": self.director_tree}, "tree"),
        ):
            with self.subTest(updates=updates), self.assertRaisesRegex(RuntimeError, message):
                resolve_default_primary_authority(self.split_settings(**updates))
        (self.project_root / "README.md").write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "clean"):
            resolve_default_primary_authority(self.split_settings())

    def test_swapped_project_execution_pins_reject_snapshot(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "HEAD and starting ref"):
            resolve_default_primary_authority(
                self.split_settings(
                    langgraph_primary_project_execution_pinned_commit_sha=self.director_commit,
                    langgraph_primary_project_execution_pinned_tree_sha=self.director_tree,
                )
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

    def test_provider_attempt_ledger_orders_formats_and_restart_blocks_ambiguity(self):
        record = self.application.create_intake(
            self.intake().model_copy(update={"source_event_id": "attempt-ledger"}),
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        reservation = self.coordinator.reserve_parallel_ready(
            task_id=record.task_id,
            policy=self.application._parallel_policy(),
        )[0]
        first = self.coordinator.reserve_provider_attempt(
            reservation=reservation,
            attempt_ordinal=1,
            format_roster=(("json", "responses-api"), ("text", "native-cli")),
            idempotency_key_digest=digest("attempt-1-key"),
            budget_evidence_digest=digest("budget"),
        )
        self.assertEqual(("json", "responses-api"), (first.format_id, first.transport_id))
        self.coordinator.record_provider_attempt(
            attempt=first,
            status=ParallelAssignmentStatus.FAILED,
            result_digest=digest("failed-result"),
            reason_code=PROVIDER_TRANSIENT_TRANSPORT_FAILURE,
        )

        restarted = PostgresParallelDispatch(self.handle.claim_store)
        writer = self.handle.claim_store.current_task_writer(record.task_id)
        assert writer is not None
        second = restarted.reserve_provider_attempt(
            reservation=reservation,
            writer=writer,
            attempt_ordinal=2,
            format_roster=(("json", "responses-api"), ("text", "native-cli")),
            idempotency_key_digest=digest("attempt-2-key"),
            budget_evidence_digest=digest("budget"),
        )
        self.assertEqual(("text", "native-cli"), (second.format_id, second.transport_id))
        with self.assertRaisesRegex(ParallelDispatchError, "reconciliation required"):
            restarted.reserve_provider_attempt(
                reservation=reservation,
                writer=writer,
                attempt_ordinal=2,
                format_roster=(("json", "responses-api"), ("text", "native-cli")),
                idempotency_key_digest=digest("attempt-2-key"),
                budget_evidence_digest=digest("budget"),
            )
        restarted.record_provider_attempt(
            attempt=second,
            writer=writer,
            status=ParallelAssignmentStatus.AMBIGUOUS,
            result_digest=None,
            reason_code="provider_outcome_unknown",
        )
        with self.assertRaisesRegex(ParallelDispatchError, "must reconcile"):
            restarted.reserve_provider_attempt(
                reservation=reservation,
                writer=writer,
                attempt_ordinal=3,
                format_roster=(("json", "responses-api"), ("text", "native-cli")),
                idempotency_key_digest=digest("attempt-3-key"),
                budget_evidence_digest=digest("budget"),
            )
        with self.handle.pool.connection() as connection:
            rows = connection.execute(
                f"SELECT attempt_ordinal, event_sequence, status, format_id, "
                f"transport_id, outcome_evidence_digest "
                f"FROM {self.schema}.director_primary_provider_attempt "
                "WHERE task_id = %s ORDER BY attempt_ordinal, event_sequence",
                (record.task_id,),
            ).fetchall()
        self.assertEqual(4, len(rows))
        self.assertEqual([1, 1, 2, 2], [row["attempt_ordinal"] for row in rows])
        self.assertTrue(rows[1]["outcome_evidence_digest"])
        self.assertTrue(rows[3]["outcome_evidence_digest"])

    def test_durable_retry_gate_uses_stored_failure_and_transport_after_restart(self):
        cases = (
            ("same-recovery", "api", "api", None, PROVIDER_TRANSIENT_TRANSPORT_FAILURE, None),
            ("terminal", "api", "cli", None, "authenticated_failure",
             "provider_retry_requires_transient_failure"),
            ("same-third", "api", "cli", "cli",
             PROVIDER_TRANSIENT_TRANSPORT_FAILURE, "provider_retry_evidence_insufficient"),
            ("unknown-third", "api", "cli", "unknown-a",
             PROVIDER_TRANSIENT_TRANSPORT_FAILURE, "provider_retry_transport_unknown"),
            ("unknown-previous", "unknown-a", "cli", None,
             PROVIDER_TRANSIENT_TRANSPORT_FAILURE, "provider_retry_transport_unknown"),
            ("valid", "api", "cli", "api",
             PROVIDER_TRANSIENT_TRANSPORT_FAILURE, None),
        )
        for label, primary, recovery, third, reason, expected_error in cases:
            with self.subTest(case=label):
                record = self.application.create_intake(
                    self.intake().model_copy(update={"source_event_id": f"durable-{label}"}),
                    intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
                )
                reservation = self.coordinator.reserve_parallel_ready(
                    task_id=record.task_id, policy=self.application._parallel_policy(),
                )[0]
                roster = (("json", primary), ("text", recovery))
                if third is not None:
                    roster += (("third", third),)
                first = self.coordinator.reserve_provider_attempt(
                    reservation=reservation, attempt_ordinal=1, format_roster=roster,
                    idempotency_key_digest=digest(f"{label}-1"),
                    budget_evidence_digest=digest("budget"),
                )
                self.coordinator.record_provider_attempt(
                    attempt=first, status=ParallelAssignmentStatus.FAILED,
                    result_digest=None, reason_code=reason,
                )
                with open_postgres_control_plane(
                    TEST_DSN, schema=self.schema, allow_insecure_local_transport=True,
                ) as restarted_handle:
                    store = PostgresParallelDispatch(restarted_handle.claim_store)
                    writer = restarted_handle.claim_store.current_task_writer(record.task_id)
                    assert writer is not None
                    def reserve(ordinal):
                        return store.reserve_provider_attempt(
                            reservation=reservation, writer=writer,
                            attempt_ordinal=ordinal, format_roster=roster,
                            idempotency_key_digest=digest(f"{label}-{ordinal}"),
                            budget_evidence_digest=digest("budget"),
                        )
                    ordinal = 2
                    if third is not None:
                        second = reserve(2)
                        store.record_provider_attempt(
                            attempt=second, writer=writer,
                            status=ParallelAssignmentStatus.FAILED,
                            result_digest=None,
                            reason_code=PROVIDER_TRANSIENT_TRANSPORT_FAILURE,
                        )
                        # Replace the store before the third attempt too.
                        store = PostgresParallelDispatch(restarted_handle.claim_store)
                        ordinal = 3
                    if expected_error:
                        with self.assertRaisesRegex(ParallelDispatchError, expected_error):
                            reserve(ordinal)
                    else:
                        self.assertEqual(ordinal, reserve(ordinal).attempt_ordinal)
                    with restarted_handle.pool.connection() as connection:
                        rows = connection.execute(
                            f"SELECT attempt_ordinal FROM "
                            f"{self.schema}.director_primary_provider_attempt "
                            "WHERE task_id = %s ORDER BY attempt_ordinal, event_sequence",
                            (record.task_id,),
                        ).fetchall()
                    expected = [1, 1] if ordinal == 2 else [1, 1, 2, 2]
                    if expected_error is None:
                        expected.append(ordinal)
                    self.assertEqual(expected, [row["attempt_ordinal"] for row in rows])

    def test_authenticated_success_blocks_all_later_reservations_after_restart(self):
        record = self.application.create_intake(
            self.intake().model_copy(update={"source_event_id": "success-ledger"}),
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        reservation = self.coordinator.reserve_parallel_ready(
            task_id=record.task_id,
            policy=self.application._parallel_policy(),
        )[0]
        roster = (("json", "api"), ("text", "cli"))
        first = self.coordinator.reserve_provider_attempt(
            reservation=reservation,
            attempt_ordinal=1,
            format_roster=roster,
            idempotency_key_digest=digest("success-key"),
            budget_evidence_digest=digest("budget"),
        )
        self.coordinator.record_provider_attempt(
            attempt=first,
            status=ParallelAssignmentStatus.SUCCEEDED,
            result_digest=digest("authenticated-success"),
            reason_code="provider_result_authenticated",
        )
        # A fresh connection pool and dispatch store cannot forget durable success.
        with open_postgres_control_plane(
            TEST_DSN, schema=self.schema, allow_insecure_local_transport=True,
        ) as restarted_handle:
            restarted = PostgresParallelDispatch(restarted_handle.claim_store)
            writer = restarted_handle.claim_store.current_task_writer(record.task_id)
            assert writer is not None
            for ordinal in (1, 2, 3):
                with self.subTest(ordinal=ordinal):
                    with self.assertRaisesRegex(
                        ParallelDispatchError, "successful provider attempt must not be repeated"
                    ):
                        restarted.reserve_provider_attempt(
                            reservation=reservation,
                            writer=writer,
                            attempt_ordinal=ordinal,
                            format_roster=roster,
                            idempotency_key_digest=digest(f"later-key-{ordinal}"),
                            budget_evidence_digest=digest("budget"),
                        )
        with self.handle.pool.connection() as connection:
            rows = connection.execute(
                f"SELECT attempt_ordinal, status FROM "
                f"{self.schema}.director_primary_provider_attempt "
                "WHERE task_id = %s ORDER BY event_sequence", (record.task_id,),
            ).fetchall()
        self.assertEqual(
            [(1, "STARTED"), (1, "SUCCEEDED")],
            [(row["attempt_ordinal"], row["status"]) for row in rows],
        )

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

    def parallel_plan(self):
        plan = preview_plan(self.intake().text, self.settings)
        template = plan.assignments[0]
        assignments = [
            template.model_copy(
                update={
                    "sequence": 1,
                    "agent": ProviderName.CLAUDE,
                    "task": "Independent left assignment",
                    "depends_on": [],
                    "retry_limit": 3,
                }
            ),
            template.model_copy(
                update={
                    "sequence": 2,
                    "agent": ProviderName.CURSOR,
                    "task": "Independent right assignment",
                    "depends_on": [],
                    "retry_limit": 3,
                }
            ),
            template.model_copy(
                update={
                    "sequence": 3,
                    "agent": ProviderName.OPENAI,
                    "task": "Dependent aggregation assignment",
                    "depends_on": [1, 2],
                    "retry_limit": 3,
                }
            ),
        ]
        return plan.model_copy(update={"assignments": assignments})

    def approve(self, application, coordinator, task_id):
        projection = coordinator.get_projection(task_id)
        assert projection is not None
        approval = projection.projection["pending_approval"]
        return application.resume_approval(
            ApprovalResume(
                task_id=task_id,
                kind=ApprovalKind(approval["kind"]),
                request_digest=approval["request_digest"],
                approval_event_id=f"approve-{task_id.lower()}",
                decision=ApprovalDecision.APPROVE,
            )
        )

    def use_parallel_graph_completion_services(self, dispatcher, *, delegation_ledger=None):
        settings = replace(
            self.settings, langgraph_primary_offline_simulation_enabled=True
        )
        services = DefaultOfflineSimulationServices(
            settings, packet_router=packet_router(settings)
        )
        coordinator = PrimaryTaskCoordinator(
            self.handle,
            services.handlers(),
            owner_token="default-primary-test-owner",
            checkpoint_namespace="default-primary-test-v1",
            application_projection_provider=services.application_projection,
        )
        coordinator.adopt_or_activate_new_intake()
        application = DefaultPrimaryApplication(
            settings,
            coordinator,
            services,
            DefaultPrimaryAuthority(SHA, digest("authority")),
            parallel_dispatcher=dispatcher,
            delegation_ledger=delegation_ledger,
            compact_view_lanes=SMALL_LANES,
        )
        self.settings = settings
        self.services = services
        self.coordinator = coordinator
        self.application = application

    def use_standing_grant(self, dispatcher):
        """Owner UOWNER in CDEFAULT, and a live grant for the small class."""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        ledger = audit_ledger(directory.name)
        self.settings = replace(
            self.settings,
            slack_owner_user_id="UOWNER",
            slack_channel_id="CDEFAULT",
            delegation_enabled=True,
            delegation_grant_json=small_grant(),
        )
        self.use_parallel_graph_completion_services(dispatcher, delegation_ledger=ledger)
        return ledger

    def attested_intake(self, event, *, text=SMALL_TEXT, on_dispatch_ready=None):
        identity = (
            hashlib.sha256(f"nonce-{event}".encode()).hexdigest(),
            hashlib.sha256(f"payload-{event}".encode()).hexdigest(),
        )
        return self.application.create_intake(
            self.intake().model_copy(update={"source_event_id": event, "text": text}),
            intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
            authenticated_replay_identity=identity,
            on_dispatch_ready=on_dispatch_ready,
        )

    @staticmethod
    def succeeding(dispatched):
        def dispatch(reservation):
            dispatched.append(reservation.sequence)
            return ParallelDispatchOutcome(
                ParallelAssignmentStatus.SUCCEEDED,
                digest(f"granted-result-{reservation.task_id}-{reservation.sequence}"),
                "provider_result_authenticated",
            )
        return dispatch

    def test_standing_grant_skips_execution_approval_and_audits(self):
        dispatched: list[int] = []
        ledger = self.use_standing_grant(self.succeeding(dispatched))

        record = self.attested_intake("granted-small")

        projection = self.coordinator.get_projection(record.task_id)
        assert projection is not None
        self.assertIsNone(projection.projection.get("pending_approval"))
        self.assertEqual([1, 2], sorted(dispatched))
        self.assertEqual("SUCCEEDED", projection.projection["terminal_classification"])
        self.assertEqual(TaskStatus.DONE, record.status)
        self.assertIn("exact_sha_review", projection.projection["visited_nodes"])
        self.assertIn("ci_security_completion", projection.projection["visited_nodes"])
        self.assertIs(
            False,
            self.coordinator._runtime.compact_state(record.task_id)[
                "execution_approval_required"],
        )
        # The dispatcher's reading matches the persisted graph flag.
        self.assertIs(False, projection.projection["application"]["execution_approval_required"])
        [entry] = ledger.delegation_audit_records(limit=10)
        self.assertIs(RecordedOutcome.PRE_AUTHORIZED, entry.outcome)
        self.assertTrue(entry.acting_agent.endswith(record.task_id))

    def test_ineligible_task_under_live_grant_still_prompts(self):
        dispatched: list[int] = []
        ledger = self.use_standing_grant(self.succeeding(dispatched))
        ready: list[str] = []

        record = self.attested_intake(
            "ineligible-small",
            text="Project: default\nCreate a bounded implementation plan.",
            on_dispatch_ready=ready.append,
        )

        projection = self.coordinator.get_projection(record.task_id)
        assert projection is not None
        self.assertEqual("execution", projection.projection["pending_approval"]["kind"])
        # An ordinary graph persists the button, and the dispatcher reads the
        # same, so no pre-authorization refusal can ever apply to it.
        self.assertIs(
            True,
            self.coordinator._runtime.compact_state(record.task_id)[
                "execution_approval_required"],
        )
        self.assertIs(True, projection.projection["application"]["execution_approval_required"])
        self.assertEqual([], ready)
        self.assertEqual([], dispatched)
        [entry] = ledger.delegation_audit_records(limit=10)
        self.assertIs(RecordedOutcome.REFUSED, entry.outcome)

    def test_granted_intake_hands_dispatch_to_the_callback(self):
        dispatched: list[int] = []
        self.use_standing_grant(self.succeeding(dispatched))
        ready: list[str] = []

        record = self.attested_intake("callback-small", on_dispatch_ready=ready.append)

        self.assertEqual([record.task_id], ready)
        self.assertEqual([], dispatched)
        projection = self.coordinator.get_projection(record.task_id)
        assert projection is not None
        self.assertIsNone(projection.projection.get("pending_approval"))
        self.assertNotEqual("NOT_STARTED", projection.projection["provider_dispatch_status"])
        recovered = self.application.recover(record.task_id)
        self.assertEqual([1, 2], sorted(dispatched))
        self.assertEqual(TaskStatus.DONE, recovered.status)

    def test_replayed_attested_intake_does_not_exercise_twice(self):
        dispatched: list[int] = []
        ledger = self.use_standing_grant(self.succeeding(dispatched))

        first = self.attested_intake("replayed-small")
        second = self.attested_intake("replayed-small")

        self.assertEqual(first.task_id, second.task_id)
        self.assertEqual([1, 2], sorted(dispatched))
        self.assertEqual(1, len(ledger.delegation_audit_records(limit=10)))

    def test_failed_granted_task_trips_the_grant(self):
        def dispatch(reservation):
            return ParallelDispatchOutcome(
                ParallelAssignmentStatus.FAILED, None, "provider_result_failed"
            )

        ledger = self.use_standing_grant(dispatch)

        failed = self.attested_intake("tripped-small-1")
        projection = self.coordinator.get_projection(failed.task_id)
        assert projection is not None
        self.assertNotIn(
            projection.projection["terminal_classification"], (None, "SUCCEEDED")
        )
        self.assert_next_exercise_trips_on(failed.task_id, ledger, "tripped-small-2")

    def assert_next_exercise_trips_on(self, failed_task_id, ledger, event):
        after = self.attested_intake(event)
        projection = self.coordinator.get_projection(after.task_id)
        assert projection is not None
        self.assertEqual("execution", projection.projection["pending_approval"]["kind"])
        pre, trip, refused = ledger.delegation_audit_records(limit=10)
        self.assertIs(RecordedOutcome.PRE_AUTHORIZED, pre.outcome)
        self.assertEqual(["delegation_trip"], [item.gate for item in trip.gate_results])
        self.assertTrue(trip.acting_agent.endswith(failed_task_id))
        self.assertIs(RecordedOutcome.REFUSED, refused.outcome)
        self.assertTrue(refused.acting_agent.endswith(after.task_id))

    def test_a_failed_aggregate_left_unclassified_trips_the_grant(self):
        """resume_parallel_aggregate raised, so the graph never classified it."""

        def dispatch(reservation):
            return ParallelDispatchOutcome(
                ParallelAssignmentStatus.FAILED, None, "provider_result_failed")

        ledger = self.use_standing_grant(dispatch)
        with patch.object(self.coordinator, "resume_parallel_aggregate",
                          side_effect=RuntimeError("resume failed")):
            try:
                self.attested_intake("unclassified-small")
            except RuntimeError:
                pass
        [pre] = ledger.delegation_audit_records(limit=10)
        failed_task_id = pre.acting_agent.removeprefix("director-langgraph:")
        projection = self.coordinator.get_projection(failed_task_id)
        assert projection is not None
        self.assertIsNone(projection.projection["terminal_classification"])
        aggregate = self.coordinator.parallel_aggregate(failed_task_id)
        self.assertTrue(aggregate.terminal)
        self.assertFalse(aggregate.succeeded)
        self.assert_next_exercise_trips_on(failed_task_id, ledger, "unclassified-next")

    def test_a_callback_settled_failure_trips_the_grant(self):
        """No drive loop runs: the task fails through record_provider_callback."""

        ledger = self.use_standing_grant(self.succeeding([]))
        ready: list[str] = []
        granted = self.attested_intake("callback-failed-small", on_dispatch_ready=ready.append)
        self.assertEqual([granted.task_id], ready)
        [first] = self.coordinator.reserve_parallel_ready(
            task_id=granted.task_id, policy=self.application._parallel_policy())
        attempt = self.coordinator.reserve_provider_attempt(
            reservation=first,
            attempt_ordinal=1,
            format_roster=(("json", "callback"),),
            idempotency_key_digest=digest("granted-callback-key"),
            budget_evidence_digest=digest("granted-callback-budget"),
        )
        self.application.record_provider_callback(
            task_id=granted.task_id,
            assignment_id=first.assignment_id,
            provider_id=first.provider_id,
            provider_run_id="run-granted-callback",
            command=ProviderSubgraphResult(
                provider_attempt_digest=attempt.attempt_digest,
                idempotency_key_digest=attempt.idempotency_key_digest,
                authority_snapshot_digest=digest("granted-callback-authority"),
                producer_id=first.provider_id,
                disposition=ProviderDisposition.FAILED,
                result_digest=None,
                candidate_sha=None,
                reason_code="callback_failed",
            ),
        )
        projection = self.coordinator.get_projection(granted.task_id)
        assert projection is not None
        self.assertNotIn(
            projection.projection["terminal_classification"], (None, "SUCCEEDED"))
        self.assert_next_exercise_trips_on(granted.task_id, ledger, "callback-failed-next")

    def test_an_ambiguous_granted_task_trips_the_grant(self):
        def dispatch(reservation):
            return ParallelDispatchOutcome(
                ParallelAssignmentStatus.AMBIGUOUS, None, "provider_outcome_unknown")

        ledger = self.use_standing_grant(dispatch)
        granted = self.attested_intake("ambiguous-small")
        projection = self.coordinator.get_projection(granted.task_id)
        assert projection is not None
        # Never terminal, so no drive-loop or callback hook would ever see it.
        self.assertIsNone(projection.projection["terminal_classification"])
        self.assertEqual(1, self.coordinator.parallel_aggregate(granted.task_id).ambiguous_slot_count)
        self.assert_next_exercise_trips_on(granted.task_id, ledger, "ambiguous-next")

    def same_lane_review_plan(self):
        """A plan whose review barrier reuses the first-wave producer's lane."""

        plan = self.parallel_plan()
        assignments = list(plan.assignments)
        assignments[2] = assignments[2].model_copy(
            update={
                # Same provider lane as assignment 1, which is exactly the shape
                # a lane-matching lookup resolves to the wrong plan member.
                "agent": ProviderName.CLAUDE,
                "kind": AssignmentKind.INDEPENDENT_REVIEW,
                "task": "Independent review of the complete first wave",
            }
        )
        return plan.model_copy(update={"assignments": assignments})

    def _record_packet_artifacts(self, record, assignment) -> str:
        """Persist one real work/result artifact pair and return its digest."""

        assignment_id = self.services._packet_router._assignment_id(record, assignment)
        work = self.services._packet_router.planned_work_packet(record, assignment)
        writer = self.handle.claim_store.current_task_writer(record.task_id)
        assert writer is not None
        self.handle.claim_store.record_work_packet_artifact(
            task_id=record.task_id,
            assignment_id=assignment_id,
            packet=work,
            owner_token=writer.owner_token,
            fencing_generation=writer.fencing_generation,
        )
        result = {
            "schema_version": "1.0",
            "task_id": record.task_id,
            "assignment_id": assignment_id,
            "idempotency_key": work["idempotency_key"],
            "authority_snapshot_digest": work["authority_snapshot_digest"],
            "correlation": dict(work["correlation"]),
            "provider": assignment.agent.value,
            "run_reference": f"run-{assignment.sequence}",
            "status": "SUCCEEDED",
            "outcome": f"assignment {assignment.sequence} evidence",
            "artifacts": [],
            "checks": [],
            "acceptance": [
                {"check": str(check), "status": "PASS", "evidence": "recorded"}
                for check in work["acceptance_checks"]
            ],
            "risks": [],
            "blockers": [],
            "requested_decisions": [],
            "side_effects": [],
            "usage": None,
            "revision_recommended": False,
        }
        persisted = self.handle.claim_store.record_result_packet_artifact(
            task_id=record.task_id,
            assignment_id=assignment_id,
            packet=result,
            owner_token=writer.owner_token,
            fencing_generation=writer.fencing_generation,
        )
        return persisted.document_digest

    def test_packet_result_resolves_the_review_not_a_same_lane_producer(self):
        digests: dict[int, str] = {}

        def dispatch(reservation):
            record = self.services._record(reservation.task_id)
            assignment = next(
                item
                for item in record.plan.assignments
                if item.sequence == reservation.sequence
            )
            recorded = self._record_packet_artifacts(record, assignment)
            digests[reservation.sequence] = recorded
            # The dependent review packet can only be built once its upstream
            # outcomes are accepted, exactly as the live dispatcher records them.
            with self.services._lock:
                stored = self.services._records[reservation.task_id]
                stored.assignment_outcomes.append(
                    AssignmentOutcome(
                        assignment_sequence=assignment.sequence,
                        provider=assignment.agent,
                        run_id=f"run-{assignment.sequence}",
                        result=f"assignment {assignment.sequence} evidence",
                        review_summary=f"assignment {assignment.sequence} evidence",
                        provider_result_digest=recorded.removeprefix("sha256:"),
                    )
                )
            return ParallelDispatchOutcome(
                ParallelAssignmentStatus.SUCCEEDED,
                recorded,
                "provider_result_authenticated",
            )

        self.use_parallel_graph_completion_services(dispatch)
        with patch(
            "director_orchestrator.primary_default.preview_plan",
            return_value=self.same_lane_review_plan(),
        ):
            pending = self.application.create_intake(
                self.intake().model_copy(update={"source_event_id": "same-lane-review"}),
                intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
            )
        self.approve(self.application, self.coordinator, pending.task_id)
        aggregate = self.coordinator.parallel_aggregate(pending.task_id)
        state = self.coordinator._runtime.compact_state(pending.task_id)
        record = self.application.get_task(pending.task_id)
        self.services.register(record)
        self.addCleanup(self.services.discard, pending.task_id)

        producer = min(record.plan.assignments, key=lambda item: item.sequence)
        review = max(record.plan.assignments, key=lambda item: item.sequence)
        self.assertEqual(
            _assignment_provider_lane(producer), _assignment_provider_lane(review)
        )
        self.assertNotEqual(digests[producer.sequence], digests[review.sequence])
        # The graph carries the aggregate digest, never an individual one.
        self.assertEqual(aggregate.aggregate_digest, state["provider_result_digest"])
        self.assertNotIn(
            state["provider_result_digest"], set(digests.values())
        )

        resolver = build_primary_packet_result_resolver(
            handle=self.handle, services=self.services
        )
        binding = resolver(
            self.handler_context(
                pending.task_id,
                scheduled_producer_id=str(state["scheduled_producer_id"]),
                provider_result_digest=str(state["provider_result_digest"]),
            )
        )

        assert binding is not None
        review_assignment_id = self.services._packet_router._assignment_id(
            record, review
        )
        self.assertEqual(review.sequence, binding.assignment.sequence)
        self.assertEqual(review_assignment_id, binding.assignment_id)
        self.assertEqual(
            digests[review.sequence], binding.outcome.result_packet_digest
        )
        self.assertEqual(
            aggregate.aggregate_digest, binding.expected_context_digest
        )
        self.assertNotEqual(
            digests[producer.sequence], binding.outcome.result_packet_digest
        )

        # A context carrying anything but the durable aggregate fails closed.
        self.assertIsNone(
            resolver(
                self.handler_context(
                    pending.task_id,
                    scheduled_producer_id=str(state["scheduled_producer_id"]),
                    provider_result_digest=digests[producer.sequence],
                )
            )
        )

    @staticmethod
    def handler_context(
        task_id: str,
        *,
        scheduled_producer_id: str,
        provider_result_digest: str,
    ) -> HandlerContext:
        return HandlerContext(
            task_id=task_id,
            source_event_id="source-same-lane",
            correlation_id="correlation-same-lane",
            intake_digest=digest("intake"),
            scope_digest=digest("scope"),
            authority_snapshot_digest=digest("authority"),
            work_packet_digest=digest("work"),
            idempotency_key_digest=digest("idempotency"),
            starting_sha=SHA,
            candidate_sha=SHA,
            plan_digest=digest("plan"),
            scheduled_producer_id=scheduled_producer_id,
            provider_attempt_digest=digest("attempt"),
            provider_dispatch_status="COMPLETED",
            provider_result_digest=provider_result_digest,
            producer_id=scheduled_producer_id,
            reviewer_id=None,
            revision_count=0,
            budget_limit_units=10,
            budget_used_units=1,
            effect_key_digest=None,
            effect_attempt_digest=None,
            writer_owner_id="langgraph",
        )

    def test_parallel_default_dispatches_all_ready_then_dependency(self):
        barrier = threading.Barrier(2)
        entered: list[int] = []
        completed: list[int] = []
        worker_threads: set[int] = set()
        lock = threading.Lock()

        def dispatch(reservation):
            with lock:
                entered.append(reservation.sequence)
                worker_threads.add(threading.get_ident())
                if reservation.sequence == 3:
                    self.assertEqual({1, 2}, set(completed))
            if reservation.sequence in {1, 2}:
                barrier.wait(timeout=5)
            with lock:
                completed.append(reservation.sequence)
            return ParallelDispatchOutcome(
                ParallelAssignmentStatus.SUCCEEDED,
                digest(f"parallel-result-{reservation.sequence}"),
                "provider_result_authenticated",
            )

        self.use_parallel_graph_completion_services(dispatch)
        with patch(
            "director_orchestrator.primary_default.preview_plan",
            return_value=self.parallel_plan(),
        ):
            pending = self.application.create_intake(
                self.intake(),
                intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
            )
        finished = self.approve(self.application, self.coordinator, pending.task_id)
        aggregate = self.coordinator.parallel_aggregate(pending.task_id)
        with self.handle.pool.connection() as connection:
            retry_limits = connection.execute(
                f"SELECT retry_limit FROM {self.schema}.director_primary_parallel_assignment "
                "WHERE task_id = %s ORDER BY sequence",
                (pending.task_id,),
            ).fetchall()

        self.assertEqual([1, 2, 3], sorted(entered))
        self.assertEqual([3, 3, 3], [row["retry_limit"] for row in retry_limits])
        self.assertGreaterEqual(len(worker_threads), 2)
        self.assertTrue(aggregate.terminal)
        self.assertTrue(aggregate.succeeded)
        self.assertEqual(TaskStatus.DONE, finished.status)
        self.assertTrue(all(value == "SUCCEEDED" for value in aggregate.statuses.values()))
        projection = self.coordinator.get_projection(pending.task_id)
        assert projection is not None
        state = self.coordinator._runtime.compact_state(pending.task_id)
        self.assertEqual(
            aggregate.aggregate_digest,
            state["provider_result_digest"],
        )
        self.assertIn("exact_sha_review", projection.projection["visited_nodes"])
        self.assertIn(
            "ci_security_completion", projection.projection["visited_nodes"]
        )
        self.assertEqual("SUCCEEDED", projection.projection["terminal_classification"])

    def _contended_wave(self, contend, *, wave_seconds=2.0):
        """Drive one real wave that contends for a row `contend` locks.

        The contended lock is taken from inside the dispatcher, so readiness
        and admission are already done: what waits is the foreground
        completion path the real coordinator runs after execution, with
        `executor.map` still holding the wave open.
        """
        from contextlib import ExitStack
        from time import monotonic

        held = ExitStack()
        self.addCleanup(held.close)
        blocked = []

        def dispatch(reservation):
            if not blocked:
                blocked.append(reservation.assignment_id)
                connection = held.enter_context(self.handle.pool.connection())
                held.enter_context(connection.transaction())
                contend(connection, reservation)
            return ParallelDispatchOutcome(
                ParallelAssignmentStatus.SUCCEEDED,
                digest(f"parallel-result-{reservation.sequence}"),
                "provider_result_authenticated",
            )

        dispatch.readiness_deadline = lambda: monotonic() + wave_seconds
        self.use_parallel_graph_completion_services(dispatch)
        with patch(
            "director_orchestrator.primary_default.preview_plan",
            return_value=self.parallel_plan(),
        ):
            pending = self.application.create_intake(
                self.intake(),
                intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
            )
        started = monotonic()
        raised: Exception | None = None
        try:
            try:
                self.approve(self.application, self.coordinator, pending.task_id)
            except Exception as error:  # returned to the caller, never swallowed
                raised = error
            elapsed = monotonic() - started
        finally:
            # Released here, not in cleanup: the schema drop in tearDown would
            # otherwise wait on this same lock.
            held.close()
        return SimpleNamespace(task_id=pending.task_id, elapsed=elapsed, raised=raised,
                                blocked=blocked[0] if blocked else None)

    def _assert_completion_bound(self, wave, *, wave_seconds=2.0):
        from director_orchestrator.primary_parallel import COMPLETION_ALLOWANCE_SECONDS

        self.assertIsNotNone(wave.blocked, "the wave never dispatched")
        self.assertIsNotNone(wave.raised,
                             "the contended completion write must not have succeeded")
        self.assertGreater(wave.elapsed, 1.0,
                            "it must have actually waited on the contended lock")
        self.assertLess(wave.elapsed, wave_seconds + COMPLETION_ALLOWANCE_SECONDS + 5.0,
                        "the completion allowance is a bound, not a licence to wait")

    def test_result_settlement_lock_is_bounded_by_the_completion_allowance(self):
        """`record_result`, called while `executor.map` still waits, is bounded.

        The wave's own bound is two seconds. Settling what already happened may
        spend the one completion allowance beyond it and nothing more, and an
        unsettled slot keeps its ACTIVE reservation rather than being released
        or guessed at.
        """
        wave = self._contended_wave(lambda connection, reservation: connection.execute(
            f"SELECT 1 FROM {self.schema}.director_primary_parallel_assignment "
            "WHERE task_id=%s AND assignment_id=%s FOR UPDATE",
            (reservation.task_id, reservation.assignment_id)))
        self._assert_completion_bound(wave)
        with self.handle.pool.connection() as connection:
            statuses = {row["assignment_id"]: row["status"] for row in connection.execute(
                f"SELECT assignment_id, status FROM {self.schema}"
                ".director_primary_parallel_assignment WHERE task_id=%s",
                (wave.task_id,)).fetchall()}
        self.assertEqual("ACTIVE", statuses[wave.blocked],
                          "an unsettled slot is retained for reconciliation")

    def test_outcome_projection_write_is_bounded_by_the_completion_allowance(self):
        """The same allowance covers publishing the wave's outcome projection."""

        wave = self._contended_wave(lambda connection, reservation: connection.execute(
            f"SELECT 1 FROM {self.schema}.director_primary_task_projection "
            "WHERE task_id=%s FOR UPDATE", (reservation.task_id,)))
        self._assert_completion_bound(wave)
        with self.handle.pool.connection() as connection:
            settled = [row["status"] for row in connection.execute(
                f"SELECT status FROM {self.schema}.director_primary_parallel_assignment "
                "WHERE task_id=%s", (wave.task_id,)).fetchall()]
        self.assertIn("SUCCEEDED", settled,
                      "the ledger keeps what it settled before the projection write")

    def test_checkpoint_completion_write_is_bounded_by_the_completion_allowance(self):
        """The checkpointer's own storage is bounded by the same cutoff.

        `record_parallel_aggregate` reads the snapshot, updates the state and
        reads the outcome on connections it acquires itself, so the cutoff is
        published for that call rather than passed to LangGraph.
        """
        wave = self._contended_wave(lambda connection, _reservation: connection.execute(
            f"LOCK TABLE {self.schema}.checkpoints IN ACCESS EXCLUSIVE MODE"))
        self._assert_completion_bound(wave)
        # Persistence that could not be confirmed is classified conservatively:
        # the call fails, nothing claims a completed wave, and the durable
        # ledger rows stay exactly as they were for reconciliation.
        projection = self.coordinator.get_projection(wave.task_id)
        self.assertNotEqual("SUCCEEDED",
                            (projection.projection.get("terminal_classification")
                             if projection is not None else None),
                            f"raised={wave.raised!r}")
        with self.handle.pool.connection() as connection:
            settled = [row["status"] for row in connection.execute(
                f"SELECT status FROM {self.schema}.director_primary_parallel_assignment "
                "WHERE task_id=%s", (wave.task_id,)).fetchall()]
        self.assertTrue(settled, "the ledger rows survive an unconfirmed checkpoint")

    def test_paid_setup_hold_preserves_approved_pause_and_recovers_ready_waves(self):
        from director_orchestrator.primary_parallel import PaidSetupHold
        ready = False
        entered = []
        checked = []
        def dispatch(reservation):
            entered.append(reservation.sequence)
            record = self.services._record(reservation.task_id)
            assignment = next(a for a in record.plan.assignments if a.sequence == reservation.sequence)
            record.assignment_outcomes.append(AssignmentOutcome(
                assignment_sequence=assignment.sequence,provider=assignment.agent,
                run_id=f"accepted-{assignment.sequence}",result=f"accepted-{assignment.sequence}",
                review_summary="synthetic accepted outcome",
                provider_result_digest=digest(f"accepted-{assignment.sequence}").removeprefix("sha256:")))
            return ParallelDispatchOutcome(ParallelAssignmentStatus.SUCCEEDED,
                digest(f"accepted-{reservation.sequence}"), "provider_result_authenticated")
        def preflight(reservation, **kwargs):
            checked.append(reservation.sequence)
            if not ready:
                raise PaidSetupHold()
            if reservation.sequence == 3:
                prior = self.coordinator.parallel_aggregate(reservation.task_id)
                self.assertEqual(len(prior.result_digests), 2)
                record = self.services._record(reservation.task_id)
                assignment = next(a for a in record.plan.assignments if a.sequence == 3)
                packet = self.services._packet_router.planned_work_packet(record,assignment)
                serialized = json.dumps(packet)
                self.assertIn(self.services._packet_router._dependency_digest(record,1),serialized)
                self.assertIn(self.services._packet_router._dependency_digest(record,2),serialized)
        dispatch.preflight = preflight
        self.use_parallel_graph_completion_services(dispatch)
        with patch("director_orchestrator.primary_default.preview_plan", return_value=self.parallel_plan()):
            pending = self.application.create_intake(self.intake(),
                intake_authentication=IntakeAuthentication.BEARER_DRY_RUN)
        held = self.approve(self.application,self.coordinator,pending.task_id)
        self.assertEqual(held.status, TaskStatus.BLOCKED)
        self.assertEqual(entered, [])
        self.assertNotIn(3,checked)
        state = self.coordinator._runtime.compact_state(pending.task_id)
        self.assertIsNone(state.get("terminal_classification"))
        self.assertEqual(tuple(state['setup_hold_reasons']), ('paid_setup_required',))
        projection = self.coordinator.get_projection(pending.task_id)
        self.assertEqual(projection.projection['run_status'],'PAUSED')
        # Recovery uses the existing approved provider interrupt, no fresh approval.
        ready = True
        final = self.application.recover(pending.task_id)
        self.assertEqual(final.status,TaskStatus.DONE)
        self.assertEqual(sorted(entered),[1,2,3])
        self.assertEqual(self.coordinator.get_projection(pending.task_id).projection['setup_hold_reasons'],[])

    def test_requested_enterprise_capability_persists_owner_hold_before_registration(self):
        settings = replace(self.settings, langgraph_primary_offline_simulation_enabled=True,
            trusted_provider_cost_ranks_json='{"openai":4,"claude":2,"cursor":1,"grok":3,"perplexity":5,"firecrawl":6}')
        services = DefaultOfflineSimulationServices(settings, packet_router=packet_router(settings), live_composition=True)
        services.available_provider_lanes = ("openai", "claude", "cursor", "grok")
        services.provider_lane_ready = lambda _provider: True
        coordinator = PrimaryTaskCoordinator(self.handle, services.handlers(),
            owner_token="default-primary-test-owner", checkpoint_namespace="capability-hold-v1",
            application_projection_provider=services.application_projection)
        coordinator.adopt_or_activate_new_intake()
        def forbidden(_reservation):
            self.fail("capability hold must precede provider dispatch")
        application = DefaultPrimaryApplication(settings, coordinator, services,
            DefaultPrimaryAuthority(SHA, digest("authority")), parallel_dispatcher=forbidden)
        held = application.create_intake(self.intake().model_copy(update={
            "primary_required_capabilities": ("enterprise_context",),
        }), intake_authentication=IntakeAuthentication.BEARER_DRY_RUN)
        self.assertEqual(held.status, TaskStatus.OWNER_DECISION_REQUIRED)
        self.assertIn("primary_roster_primary_enterprise_scoped_adapter_required", held.workload_selection_hold_reasons)
        with self.handle.pool.connection() as connection:
            count = connection.execute(f"SELECT count(*) AS n FROM {self.schema}.director_primary_provider_attempt").fetchone()["n"]
        self.assertEqual(count, 0)
        # A stale policy digest is retained and surfaced as an owner hold.
        from director_orchestrator.primary_workload import POLICY_VERSION
        with self.handle.pool.connection() as connection:
            connection.execute(f"""INSERT INTO {self.schema}.director_primary_workload_assignment
                (task_id, assignment_id, provider_lane, reservation_digest,
                 fencing_generation, policy_version, policy_digest)
                VALUES ('old-history','old-assignment','claude',%s,1,%s,%s)""",
                (digest("old-reservation"), POLICY_VERSION, digest("old-policy")))
        incompatible = application.create_intake(self.intake().model_copy(update={
            "source_event_id": "incompatible-history", "primary_required_capabilities": (),
        }), intake_authentication=IntakeAuthentication.BEARER_DRY_RUN)
        self.assertEqual(incompatible.status, TaskStatus.OWNER_DECISION_REQUIRED)
        self.assertEqual(incompatible.workload_selection_hold_reasons,
                         ["primary_roster_workload_history_invalid"])
        with self.handle.pool.connection() as connection:
            row = connection.execute(f"SELECT policy_digest FROM {self.schema}.director_primary_workload_assignment WHERE task_id='old-history'").fetchone()
        self.assertEqual(row["policy_digest"], digest("old-policy"))

    def test_live_primary_postgres_dispatches_minimal_independent_capability_roster(self):
        settings = replace(
            self.settings,
            langgraph_primary_offline_simulation_enabled=True,
            trusted_provider_cost_ranks_json='{"openai":4,"claude":2,"cursor":1,"grok":3,"perplexity":5,"firecrawl":6}',
        )
        first_wave_size = 1
        barrier = threading.Barrier(first_wave_size)
        completed_first_wave: set[int] = set()
        entered: list[tuple[int, str]] = []
        worker_threads: set[int] = set()
        lock = threading.Lock()

        def dispatch(reservation):
            with lock:
                entered.append((reservation.sequence, reservation.provider_id))
                worker_threads.add(threading.get_ident())
                if reservation.sequence == first_wave_size + 1:
                    self.assertEqual(
                        set(range(1, first_wave_size + 1)), completed_first_wave
                    )
            if reservation.sequence <= first_wave_size:
                barrier.wait(timeout=10)
                with lock:
                    completed_first_wave.add(reservation.sequence)
            return ParallelDispatchOutcome(
                ParallelAssignmentStatus.SUCCEEDED,
                digest(f"all-lane:{reservation.assignment_id}"),
                "provider_result_authenticated",
            )

        services = DefaultOfflineSimulationServices(
            settings,
            packet_router=packet_router(settings),
            live_composition=True,
        )
        services.available_provider_lanes = ("openai", "claude", "cursor", "grok")
        services.provider_lane_ready = lambda _provider: True
        coordinator = PrimaryTaskCoordinator(
            self.handle,
            services.handlers(),
            owner_token="default-primary-test-owner",
            checkpoint_namespace="default-primary-all-lanes-v1",
            application_projection_provider=services.application_projection,
        )
        coordinator.adopt_or_activate_new_intake()
        application = DefaultPrimaryApplication(
            settings,
            coordinator,
            services,
            DefaultPrimaryAuthority(SHA, digest("authority")),
            parallel_dispatcher=dispatch,
        )

        pending = application.create_intake(
            self.intake().model_copy(update={"source_event_id": "all-lane-live-wave"}),
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        assert pending.plan is not None
        first_wave = pending.plan.assignments[:first_wave_size]
        review = pending.plan.assignments[first_wave_size]
        self.assertEqual(
            {"cursor"},
            {item.primary_provider_lane for item in first_wave},
        )
        self.assertTrue(all(not item.depends_on for item in first_wave))
        self.assertEqual(
            list(range(1, first_wave_size + 1)), review.depends_on
        )

        finished = self.approve(application, coordinator, pending.task_id)
        aggregate = coordinator.parallel_aggregate(pending.task_id)
        with self.handle.pool.connection() as connection:
            rows = connection.execute(
                f"SELECT sequence, provider_id, status FROM "
                f"{self.schema}.director_primary_parallel_assignment "
                "WHERE task_id = %s ORDER BY sequence",
                (pending.task_id,),
            ).fetchall()

        self.assertEqual(first_wave_size + 1, len(rows))
        self.assertEqual(
            {"cursor"},
            {row["provider_id"] for row in rows[:first_wave_size]},
        )
        self.assertGreaterEqual(len(worker_threads), first_wave_size)
        self.assertTrue(all(row["status"] == "SUCCEEDED" for row in rows))
        self.assertTrue(aggregate.succeeded)
        self.assertEqual(TaskStatus.DONE, finished.status)

    def test_parallel_restart_resumes_pending_rows_without_duplicate_dispatch(self):
        self.use_parallel_graph_completion_services(None)
        with patch(
            "director_orchestrator.primary_default.preview_plan",
            return_value=self.parallel_plan(),
        ):
            pending = self.application.create_intake(
                self.intake().model_copy(update={"source_event_id": "parallel-restart"}),
                intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
            )
        self.approve(self.application, self.coordinator, pending.task_id)
        self.assertTrue(
            all(
                value == "PENDING"
                for value in self.coordinator.parallel_aggregate(pending.task_id).statuses.values()
            )
        )

        calls: list[str] = []

        def dispatch(reservation):
            calls.append(reservation.assignment_id)
            return ParallelDispatchOutcome(
                ParallelAssignmentStatus.SUCCEEDED,
                digest("restart:" + reservation.assignment_id),
                "provider_result_authenticated",
            )

        restarted_services = DefaultOfflineSimulationServices(
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
        restarted = DefaultPrimaryApplication(
            self.settings,
            restarted_coordinator,
            restarted_services,
            DefaultPrimaryAuthority(SHA, digest("authority")),
            parallel_dispatcher=dispatch,
        )

        first = restarted.recover(pending.task_id)
        second = restarted.recover(pending.task_id)

        self.assertEqual(3, len(calls))
        self.assertEqual(3, len(set(calls)))
        self.assertEqual(TaskStatus.DONE, first.status)
        self.assertEqual(TaskStatus.DONE, second.status)

    def test_parallel_task_is_not_terminal_until_every_assignment_settles(self):
        def dispatch(reservation):
            if reservation.sequence == 2:
                return ParallelDispatchOutcome(
                    ParallelAssignmentStatus.AMBIGUOUS,
                    None,
                    "provider_outcome_unknown",
                )
            return ParallelDispatchOutcome(
                ParallelAssignmentStatus.SUCCEEDED,
                digest(f"partial-{reservation.sequence}"),
                "provider_result_authenticated",
            )

        self.application._parallel_dispatcher = dispatch
        with patch(
            "director_orchestrator.primary_default.preview_plan",
            return_value=self.parallel_plan(),
        ):
            pending = self.application.create_intake(
                self.intake().model_copy(update={"source_event_id": "parallel-partial"}),
                intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
            )
        result = self.approve(self.application, self.coordinator, pending.task_id)
        aggregate = self.coordinator.parallel_aggregate(pending.task_id)

        self.assertFalse(aggregate.terminal)
        self.assertFalse(aggregate.succeeded)
        self.assertEqual(1, aggregate.ambiguous_slot_count)
        self.assertNotEqual(TaskStatus.DONE, result.status)
        self.assertIn("PENDING", aggregate.statuses.values())

    def test_parallel_restart_classifies_active_without_redispatch(self):
        with patch(
            "director_orchestrator.primary_default.preview_plan",
            return_value=self.parallel_plan(),
        ):
            pending = self.application.create_intake(
                self.intake().model_copy(update={"source_event_id": "parallel-active"}),
                intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
            )
        self.application.settings = replace(
            self.settings, langgraph_primary_offline_simulation_enabled=True
        )
        self.approve(self.application, self.coordinator, pending.task_id)
        reservations = self.coordinator.reserve_parallel_ready(
            task_id=pending.task_id,
            policy=self.application._parallel_policy(),
        )
        self.assertEqual(2, len(reservations))

        calls: list[str] = []
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
        restarted = DefaultPrimaryApplication(
            self.settings,
            restarted_coordinator,
            restarted_services,
            DefaultPrimaryAuthority(SHA, digest("authority")),
            parallel_dispatcher=lambda reservation: calls.append(
                reservation.assignment_id
            ),  # type: ignore[arg-type]
        )

        result = restarted.recover(pending.task_id)
        aggregate = restarted_coordinator.parallel_aggregate(pending.task_id)
        projection = restarted_coordinator.get_projection(pending.task_id)

        self.assertEqual([], calls)
        self.assertEqual(2, aggregate.ambiguous_slot_count)
        self.assertFalse(aggregate.terminal)
        self.assertNotEqual(TaskStatus.DONE, result.status)
        assert projection is not None
        self.assertEqual(2, projection.projection["parallel_ambiguous_slot_count"])

    def test_parallel_cancellation_fences_new_work_before_graph_cancel(self):
        with patch(
            "director_orchestrator.primary_default.preview_plan",
            return_value=self.parallel_plan(),
        ):
            pending = self.application.create_intake(
                self.intake().model_copy(update={"source_event_id": "parallel-cancel-race"}),
                intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
            )
        active = self.coordinator.reserve_parallel_ready(
            task_id=pending.task_id,
            policy=self.application._parallel_policy(),
        )
        self.assertEqual(2, len(active))

        cancelled = self.application.cancel(pending.task_id)
        aggregate = self.coordinator.parallel_aggregate(pending.task_id)
        self.assertEqual(TaskStatus.FAILED, cancelled.status)
        self.assertEqual(2, aggregate.ambiguous_slot_count)
        self.assertEqual(
            (),
            self.coordinator.reserve_parallel_ready(
                task_id=pending.task_id,
                policy=self.application._parallel_policy(),
            ),
        )
        projection = self.coordinator.get_projection(pending.task_id)
        assert projection is not None
        self.assertEqual("CANCELLED", projection.projection["terminal_classification"])

    def test_recover_repairs_missing_parallel_registration_after_intake_crash(self):
        pending = self.application.create_intake(
            self.intake().model_copy(update={"source_event_id": "intake-registration-repair"}),
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        with self.handle.pool.connection() as connection:
            with connection.transaction():
                connection.execute(
                    f"DELETE FROM {self.schema}.director_primary_parallel_assignment "
                    "WHERE task_id = %s",
                    (pending.task_id,),
                )
                connection.execute(
                    f"DELETE FROM {self.schema}.director_primary_parallel_batch "
                    "WHERE task_id = %s",
                    (pending.task_id,),
                )

        repaired = self.application.recover(pending.task_id)
        aggregate = self.coordinator.parallel_aggregate(pending.task_id)
        self.assertEqual(pending.task_id, repaired.task_id)
        self.assertTrue(aggregate.statuses)
        projection = self.coordinator.get_projection(pending.task_id)
        assert projection is not None
        self.assertEqual(
            projection.projection["parallel_plan_digest"],
            self.coordinator._runtime.compact_state(pending.task_id)[
                "parallel_plan_digest"
            ],
        )

    def test_callback_settles_only_exact_assignment_attempt_not_graph_edge(self):
        # Disposable fixture history only; this does not initialize production.
        from tests.test_primary_workload import HISTORY
        from director_orchestrator.primary_workload import POLICY_DIGEST, POLICY_VERSION
        with self.handle.claim_store._pool.connection() as connection:
            for index, provider in enumerate(HISTORY):
                connection.execute(f"""INSERT INTO {self.schema}.director_primary_workload_assignment
                    (task_id, assignment_id, provider_lane, reservation_digest,
                     fencing_generation, policy_version, policy_digest)
                    VALUES (%s,%s,%s,%s,1,%s,%s)""",
                    ("test-workload-fixture", str(index), provider, digest("fixture"),
                     POLICY_VERSION, POLICY_DIGEST))
        with patch(
            "director_orchestrator.primary_default.preview_plan",
            return_value=self.parallel_plan(),
        ):
            pending = self.application.create_intake(
                self.intake().model_copy(update={"source_event_id": "parallel-callback"}),
                intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
            )
        self.application.settings = replace(
            self.settings, langgraph_primary_offline_simulation_enabled=True
        )
        self.approve(self.application, self.coordinator, pending.task_id)
        reservations = self.coordinator.reserve_parallel_ready(
            task_id=pending.task_id,
            policy=self.application._parallel_policy(),
        )
        first, second = reservations
        attempt = self.coordinator.reserve_provider_attempt(
            reservation=first,
            attempt_ordinal=1,
            format_roster=(("json", "callback"),),
            idempotency_key_digest=digest("callback-key"),
            budget_evidence_digest=digest("callback-budget"),
        )
        command = ProviderSubgraphResult(
            provider_attempt_digest=attempt.attempt_digest,
            idempotency_key_digest=attempt.idempotency_key_digest,
            authority_snapshot_digest=digest("callback-authority"),
            producer_id=first.provider_id,
            disposition=ProviderDisposition.COMPLETED,
            result_digest=digest("callback-result"),
            candidate_sha=SHA,
            reason_code="callback_authenticated",
        )
        with self.assertRaisesRegex(ParallelDispatchError, "unknown"):
            self.application.record_provider_callback(
                task_id=pending.task_id,
                assignment_id=second.assignment_id,
                provider_id=second.provider_id,
                provider_run_id="run-wrong-assignment",
                command=command,
            )

        self.application.record_provider_callback(
            task_id=pending.task_id,
            assignment_id=first.assignment_id,
            provider_id=first.provider_id,
            provider_run_id="run-exact-attempt",
            command=command,
        )
        # Exact redelivery is idempotent; the same attempt with another remote
        # run identity is a binding conflict even when every other field agrees.
        self.application.record_provider_callback(
            task_id=pending.task_id,
            assignment_id=first.assignment_id,
            provider_id=first.provider_id,
            provider_run_id="run-exact-attempt",
            command=command,
        )
        with self.assertRaisesRegex(ParallelDispatchError, "run identity conflicts"):
            self.application.record_provider_callback(
                task_id=pending.task_id,
                assignment_id=first.assignment_id,
                provider_id=first.provider_id,
                provider_run_id="run-rebound",
                command=command,
            )
        aggregate = self.coordinator.parallel_aggregate(pending.task_id)
        self.assertEqual("SUCCEEDED", aggregate.statuses[first.assignment_id])
        self.assertEqual("ACTIVE", aggregate.statuses[second.assignment_id])
        state = self.coordinator._runtime.compact_state(pending.task_id)
        self.assertIsNone(state["provider_result_receipt_digest"])
        with self.handle.pool.connection() as connection:
            callback_rows = connection.execute(
                f"SELECT provider_run_id_digest FROM "
                f"{self.schema}.director_primary_provider_attempt "
                "WHERE task_id = %s AND assignment_id = %s "
                "AND event_sequence = 3",
                (pending.task_id, first.assignment_id),
            ).fetchall()
        self.assertEqual(1, len(callback_rows))
        self.assertEqual(
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    {"provider_run_id": "run-exact-attempt"},
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                    default=str,
                ).encode("utf-8")
            ).hexdigest(),
            callback_rows[0]["provider_run_id_digest"],
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

    def test_in_flight_planning_survives_concurrent_recovery_and_cleanup(self) -> None:
        planning_entered = threading.Event()
        recovery_finished = threading.Event()
        captured: dict[str, object] = {}
        errors: list[BaseException] = []
        original_record = self.services._record

        def gated_record(task_id: str):
            record = original_record(task_id)
            if sys._getframe(1).f_code.co_name == "plan" and not planning_entered.is_set():
                captured["task_id"] = task_id
                planning_entered.set()
                if not recovery_finished.wait(timeout=10):
                    raise TimeoutError("concurrent recovery did not finish during planning")
            return record

        def run_recovery() -> None:
            try:
                if not planning_entered.wait(timeout=10):
                    raise TimeoutError("planning did not reach the shared record read")
                task_id = str(captured["task_id"])
                self.assertTrue(self.services.occupied(task_id))
                captured["recovered"] = self.application.recover(task_id)
            except BaseException as exc:
                errors.append(exc)
            finally:
                recovery_finished.set()

        self.services._record = gated_record
        worker = threading.Thread(target=run_recovery)
        worker.start()
        try:
            record = self.application.create_intake(
                self.intake().model_copy(
                    update={"source_event_id": "event-default-lifetime"}
                ),
                intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
            )
        finally:
            self.services._record = original_record
            worker.join(timeout=10)

        self.assertFalse(worker.is_alive(), "recovery worker did not finish")
        self.assertFalse(errors, errors)
        self.assertEqual(TaskStatus.OWNER_DECISION_REQUIRED, record.status)
        self.assertGreaterEqual(record.plan_generation, 1)
        assert record.plan is not None
        assignment = min(record.plan.assignments, key=lambda item: item.sequence)
        expected_packet = self.services._packet_router.work_packet(record, assignment)
        graph_state = self.coordinator._runtime.compact_state(record.task_id)
        self.assertEqual(
            document_digest(expected_packet), graph_state["work_packet_digest"]
        )
        projection = self.coordinator.get_projection(record.task_id)
        assert projection is not None
        self.assertNotEqual(
            "plan_handler_error", projection.projection.get("reason_code")
        )
        self.assertEqual(
            "execution_approval_required", projection.projection["reason_code"]
        )
        self.assertEqual(
            record.plan_generation,
            projection.projection["application"]["task_record"]["plan_generation"],
        )
        self.assertNotIn(record.task_id, self.services._records)

        recovered = self.application.recover(record.task_id)
        self.assertEqual(record.task_id, recovered.task_id)
        self.assertEqual(TaskStatus.OWNER_DECISION_REQUIRED, recovered.status)
        self.assertEqual(record.plan_generation, recovered.plan_generation)
        self.assertEqual(
            document_digest(expected_packet),
            self.coordinator._runtime.compact_state(record.task_id)["work_packet_digest"],
        )

        cancelled = self.application.cancel(record.task_id)
        self.assertEqual(TaskStatus.FAILED, cancelled.status)
        cancelled_projection = self.coordinator.get_projection(record.task_id)
        assert cancelled_projection is not None
        self.assertEqual(
            "CANCELLED", cancelled_projection.projection["terminal_classification"]
        )
        self.assertNotIn(record.task_id, self.services._records)

        later = self.application.create_intake(
            self.intake().model_copy(
                update={"source_event_id": "event-default-lifetime-later"}
            ),
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        self.assertNotEqual(record.task_id, later.task_id)
        self.assertEqual(TaskStatus.OWNER_DECISION_REQUIRED, later.status)
        self.assertNotIn(later.task_id, self.services._records)
        later_recovered = self.application.recover(later.task_id)
        self.assertEqual(later.task_id, later_recovered.task_id)
        self.assertEqual(TaskStatus.OWNER_DECISION_REQUIRED, later_recovered.status)

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

    def _offline_ledger_application(self, *, namespace, dispatch, event_id):
        """Build the Default production wiring: ledger-only provider dispatch."""

        settings = replace(
            self.settings,
            langgraph_primary_offline_simulation_enabled=True,
        )
        services = DefaultOfflineSimulationServices(
            settings, packet_router=packet_router(settings)
        )
        coordinator = PrimaryTaskCoordinator(
            self.handle,
            services.handlers(),
            owner_token="default-primary-test-owner",
            checkpoint_namespace=namespace,
            # Production wiring: the graph's single-item provider edge is an
            # approval interrupt, never a second transport owner.
            auto_execute_provider=False,
            application_projection_provider=services.application_projection,
        )
        coordinator.adopt_or_activate_new_intake()
        application = DefaultPrimaryApplication(
            settings,
            coordinator,
            services,
            DefaultPrimaryAuthority(
                starting_sha=SHA, authority_snapshot_digest=digest("authority")
            ),
            parallel_dispatcher=(
                build_offline_parallel_dispatcher(services, lambda: coordinator)
                if dispatch
                else None
            ),
        )
        pending = application.create_intake(
            self.intake().model_copy(update={"source_event_id": event_id}),
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
                approval_event_id=f"approve-{event_id}",
                decision=ApprovalDecision.APPROVE,
            )
        )
        return services, coordinator, application, pending, completed

    def test_offline_doubles_execute_through_the_parallel_ledger(self):
        """The offline wave must run under the production ledger-only wiring."""

        services, coordinator, _application, pending, completed = (
            self._offline_ledger_application(
                namespace="default-primary-offline-ledger-v1",
                dispatch=True,
                event_id="event-offline-ledger",
            )
        )

        aggregate = coordinator.parallel_aggregate(pending.task_id)
        self.assertTrue(aggregate.terminal, aggregate)
        self.assertTrue(aggregate.succeeded, aggregate)
        self.assertEqual(0, aggregate.ambiguous_slot_count)
        self.assertTrue(
            aggregate.statuses
            and all(
                status == ParallelAssignmentStatus.SUCCEEDED
                for status in aggregate.statuses.values()
            ),
            aggregate.statuses,
        )
        # Every dispatched lane ran its double exactly once, through the ledger.
        self.assertTrue(services.provider_calls > 0, services.provider_call_counts)
        self.assertTrue(
            all(value <= 1 for value in services.provider_call_counts.values()),
            services.provider_call_counts,
        )
        self.assertEqual(TaskStatus.DONE, completed.status)
        self.assertTrue(completed.assignment_outcomes)

    def test_offline_ledger_without_a_dispatcher_executes_no_provider(self):
        """Regression guard for the Stage A M0 finding.

        With auto_execute_provider=False and no ledger dispatcher, approval
        succeeded but the provider wave never ran. Ownership and approval are
        not execution, so this configuration must not report a finished task.
        """

        services, coordinator, _application, pending, completed = (
            self._offline_ledger_application(
                namespace="default-primary-offline-nodispatch-v1",
                dispatch=False,
                event_id="event-offline-nodispatch",
            )
        )

        self.assertEqual(0, services.provider_calls, services.provider_call_counts)
        aggregate = coordinator.parallel_aggregate(pending.task_id)
        self.assertFalse(aggregate.succeeded, aggregate)
        self.assertNotEqual(TaskStatus.DONE, completed.status)

    def test_unconfigured_lane_settles_failed_without_calling_a_double(self):
        """An unconfigured lane must settle FAILED, never AMBIGUOUS.

        Required by the independent review of #122/#126: raising here reaches
        the ledger as AMBIGUOUS, which retains the concurrency slot and stops
        the lifecycle from reaching a terminal state, for a failure that is
        already known before any execution starts.
        """

        settings = replace(
            self.settings, langgraph_primary_offline_simulation_enabled=True
        )
        services = DefaultOfflineSimulationServices(
            settings, packet_router=packet_router(settings)
        )
        dispatch = build_offline_parallel_dispatcher(services, lambda: None)
        outcome = dispatch(
            ParallelReservation(
                task_id="DIR-LG-UNCONFIGURED",
                assignment_id="assignment-not-a-lane",
                sequence=1,
                provider_id="not-a-lane",
                deployment_id="deployment-test",
                reservation_digest=digest("reservation:not-a-lane"),
            )
        )

        self.assertEqual(ParallelAssignmentStatus.FAILED, outcome.status)
        self.assertIsNone(outcome.result_digest)
        self.assertEqual("offline_provider_double_missing", outcome.reason_code)
        self.assertEqual(0, services.provider_calls, services.provider_call_counts)

    def test_malformed_double_digest_is_rejected_not_wrapped(self):
        """A prefix-only check let "sha256:x" settle the ledger as SUCCEEDED.

        The bad value was wrapped into a newly valid digest, so the graph could
        report DONE while the double had violated its declared result contract.
        """

        settings = replace(
            self.settings, langgraph_primary_offline_simulation_enabled=True
        )
        lane = sorted(settings.langgraph_primary_required_providers)[0]
        services = DefaultOfflineSimulationServices(
            settings,
            packet_router=packet_router(settings),
            provider_doubles={
                provider: (lambda _context: "sha256:x")
                for provider in settings.langgraph_primary_required_providers
            },
        )

        with self.assertRaisesRegex(RuntimeError, "invalid digest"):
            services.dispatch_ledger_double(
                provider=lane,
                context=SimpleNamespace(task_id="DIR-LG-MALFORMED"),
                reservation_digest=digest("reservation:malformed"),
            )

    def test_malformed_double_digest_is_rejected_in_the_provider_subgraph(self):
        """The sibling path kept the prefix-only check "sha256:x" slips through.

        The ledger path was hardened alone, so the graph's own provider edge
        still folded a malformed digest into its aggregate result digest.
        """

        settings = replace(
            self.settings, langgraph_primary_offline_simulation_enabled=True
        )
        services = DefaultOfflineSimulationServices(
            settings,
            packet_router=packet_router(settings),
            provider_doubles={
                provider: (lambda _context: "sha256:x")
                for provider in settings.langgraph_primary_required_providers
            },
        )

        with self.assertRaisesRegex(RuntimeError, "invalid digest"):
            services.provider_subgraph(
                SimpleNamespace(
                    task_id="DIR-LG-MALFORMED-SUBGRAPH",
                    provider_attempt_digest=digest("attempt:malformed"),
                    scheduled_producer_id="producer-malformed",
                    work_packet_digest=digest("packet:malformed"),
                    idempotency_key_digest=digest("idempotency:malformed"),
                    authority_snapshot_digest=digest("authority:malformed"),
                    starting_sha="0" * 40,
                )
            )

    def test_simulated_outcomes_carry_the_charter_simulation_label(self):
        """Charter: a projection that contacted no provider must be labelled.

        These strings reach the human-visible TaskRecord projection, so without
        the verbatim label a simulated wave reads like real provider evidence.
        """

        charter = (
            Path(__file__).resolve().parents[1]
            / "docs"
            / "team-operating-charter.md"
        ).read_text(encoding="utf-8")
        self.assertIn(_SIMULATION_LABEL, charter)

        _services, _coordinator, _application, _pending, completed = (
            self._offline_ledger_application(
                namespace="default-primary-offline-label-v1",
                dispatch=True,
                event_id="event-offline-label",
            )
        )

        self.assertTrue(completed.assignment_outcomes)
        for outcome in completed.assignment_outcomes:
            self.assertIn(_SIMULATION_LABEL, outcome.result)
            self.assertIn(_SIMULATION_LABEL, outcome.review_summary)

    def fresh_witness(self, task_id: str) -> dict:
        import psycopg
        from psycopg import sql
        # A new physical connection proves visibility beyond the insert transaction.
        dsn = os.environ.get("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN", "").strip()
        with psycopg.connect(dsn, connect_timeout=5, autocommit=True) as connection:
            connection.execute("SET statement_timeout = '10s'")
            row = connection.execute(sql.SQL(
                "SELECT projection FROM {}.director_primary_task_projection WHERE task_id=%s"
            ).format(sql.Identifier(self.schema)), (task_id,)).fetchall()
            dedup = connection.execute(sql.SQL(
                "SELECT task_id FROM {}.director_primary_intake_dedup WHERE task_id=%s"
            ).format(sql.Identifier(self.schema)), (task_id,)).fetchall()
        projected = row[0][0] if len(row) == 1 else {}
        return {
            "projection_rows": len(row), "dedup_rows": len(dedup),
            "reason_code": projected.get("reason_code"),
            "provider_dispatch_status": projected.get("provider_dispatch_status"),
        }

    # Tables a read-only qualification readback must never touch.
    READBACK_MUTATION_WITNESS_TABLES = (
        "director_primary_task_projection", "director_primary_task_writer", "director_primary_claim",
        "director_primary_budget", "director_primary_effect_reservation", "director_primary_effect_execution",
        "director_primary_remote_binding", "director_primary_terminal_evidence",
        "director_primary_reconciliation_event", "director_primary_slack_delivery",
        "director_primary_slack_status_receipt", "director_primary_parallel_assignment",
        "director_primary_provider_attempt", "director_primary_command_receipt", "director_primary_audit_event",
    )

    def mutation_witness(self) -> dict:
        import psycopg
        from psycopg import sql
        dsn = os.environ.get("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN", "").strip()
        counts = {}
        with psycopg.connect(dsn, connect_timeout=5, autocommit=True) as connection:
            for table in self.READBACK_MUTATION_WITNESS_TABLES:
                counts[table] = connection.execute(sql.SQL("SELECT count(*) FROM {}.{}").format(
                    sql.Identifier(self.schema), sql.Identifier(table))).fetchone()[0]
            counts["writer_leases"] = connection.execute(sql.SQL(
                "SELECT coalesce(string_agg(task_id || ':' || fencing_generation::text, ',' ORDER BY task_id), '') "
                "FROM {}.director_primary_task_writer").format(sql.Identifier(self.schema))).fetchone()[0]
        return counts

    def test_opt_in_qualification_readback_is_typed_bounded_read_only_and_keeps_default_get_unchanged(self):
        from fastapi.testclient import TestClient
        from director_orchestrator import main
        from director_orchestrator.models import QualificationEvidenceReadback, TaskRecord

        settings = replace(self.settings, shared_secret="standalone-qa-synthetic-secret",
                           langgraph_primary_new_intake_enabled=True)
        activation = SimpleNamespace(application=self.application, coordinator=self.coordinator)
        headers = {"Authorization": "Bearer standalone-qa-synthetic-secret"}
        record = self.application.create_intake(
            self.intake(), intake_authentication=IntakeAuthentication.BEARER_DRY_RUN)
        with patch.object(main, "settings", settings), \
             patch.object(main, "primary_default_activation", activation):
            client = TestClient(main.app)
            try:
                default = client.get(f"/v1/tasks/{record.task_id}", headers=headers)
                self.assertEqual(200, default.status_code, default.text)
                self.assertNotIn("qualification_evidence", default.json())
                self.assertNotIn("X-Primary-Qualification-Readback", default.headers)
                # The strict persisted contract still parses the default response exactly.
                self.assertEqual(record.task_id, TaskRecord.model_validate(default.json()).task_id)
                before = self.mutation_witness()
                opted = client.get(f"/v1/tasks/{record.task_id}", headers=headers,
                                   params={"include_qualification_evidence": "true"})
                self.assertEqual(200, opted.status_code, opted.text)
                self.assertEqual(before, self.mutation_witness())
                body = opted.json()
                evidence = body.pop("qualification_evidence")
                self.assertEqual(set(default.json()), set(body))
                for key in ("task_id", "status", "intake", "intake_authentication", "plan_generation", "provider_runs"):
                    self.assertEqual(default.json()[key], body[key])
                # The ordinary TaskRecord contract forbids the readback field: it is response-only.
                with self.assertRaises(Exception):
                    TaskRecord.model_validate({**body, "qualification_evidence": evidence})
                typed = QualificationEvidenceReadback.model_validate(evidence)
                self.assertEqual(record.task_id, typed.task_id)
                self.assertEqual("langgraph", typed.engine_owner)
                self.assertEqual(default.headers["X-Primary-Projection-Digest"], typed.projection_digest)
                self.assertEqual(int(default.headers["X-Primary-Projection-Version"]), typed.projection_version)
                self.assertEqual(record.intake.source_event_id, typed.source_event_id)
                self.assertEqual("complete", opted.headers["X-Primary-Qualification-Readback"])
                self.assertTrue(typed.complete)
                self.assertEqual([], typed.truncated)
                self.assertIn(typed.absent, ([], ["provider_usage"]))  # usage tables are optional here
                self.assertEqual(200, typed.bounds.row_limit)
                self.assertEqual(262144, typed.bounds.byte_limit)
                self.assertLess(typed.bounds.elapsed_seconds, typed.bounds.wall_seconds)
                # A fresh intake has no attempts, effects, deliveries or status receipts yet.
                self.assertEqual(([], [], [], [], []), (typed.provider_attempts, typed.effects,
                    typed.reconciliation_events, typed.status_receipts, typed.provider_usage))
                self.assertEqual("langgraph", opted.headers["X-Task-Engine-Owner"])
                missing = client.get("/v1/tasks/DIR-LG-0000000000", headers=headers,
                                     params={"include_qualification_evidence": "true"})
                self.assertEqual(404, missing.status_code)
                self.assertEqual(401, client.get(f"/v1/tasks/{record.task_id}",
                                                 params={"include_qualification_evidence": "true"}).status_code)
            finally:
                client.close()

    def test_qualification_readback_store_query_is_read_only_bounded_and_pinned(self):
        record = self.application.create_intake(
            self.intake(), intake_authentication=IntakeAuthentication.BEARER_DRY_RUN)
        store = self.coordinator._store
        for kwargs in ({"row_limit": 0}, {"row_limit": 1001}, {"byte_limit": 10}, {"wall_seconds": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                store.read_primary_qualification_evidence(record.task_id, **kwargs)
        self.assertIsNone(store.read_primary_qualification_evidence("DIR-LG-0000000000"))
        before = self.mutation_witness()
        document = store.read_primary_qualification_evidence(record.task_id)
        self.assertEqual(before, self.mutation_witness())
        self.assertEqual(record.task_id, document["task_id"])
        self.assertTrue(document["complete"])
        # Deliveries for this task are pinned; enqueueing one makes it visible and
        # a one-row limit reports truncation instead of a silently partial list.
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"""INSERT INTO {self.schema}.director_primary_slack_delivery
                    (task_id, notification_key, projection_digest, payload, client_msg_id, state)
                    VALUES (%s, 'terminal', %s, '{{}}'::jsonb, %s, 'SUPPRESSED'),
                           (%s, 'hold:synthetic', %s, '{{}}'::jsonb, %s, 'SUPPRESSED')""",
                (record.task_id, document["projection_digest"], "synthetic-" + record.task_id + "-a",
                 record.task_id, document["projection_digest"], "synthetic-" + record.task_id + "-b"))
        full = store.read_primary_qualification_evidence(record.task_id)
        self.assertEqual({"terminal", "hold:synthetic"}, {row["notification_key"] for row in full["slack_deliveries"]})
        self.assertTrue(all(row["state"] == "SUPPRESSED" for row in full["slack_deliveries"]))
        bounded = store.read_primary_qualification_evidence(record.task_id, row_limit=1)
        self.assertFalse(bounded["complete"])
        # The planned assignments of a fresh intake may also exceed one row.
        self.assertIn("slack_deliveries", bounded["truncated"])
        self.assertTrue(set(bounded["truncated"]) <= {"assignments", "slack_deliveries"}, bounded["truncated"])
        self.assertEqual(1, len(bounded["slack_deliveries"]))
        self.assertLessEqual(len(bounded["assignments"]), 1)
        tiny = store.read_primary_qualification_evidence(record.task_id, byte_limit=1024)
        self.assertFalse(tiny["complete"])
        self.assertIn("response_bytes", tiny["truncated"])
        self.assertEqual([], tiny["slack_deliveries"])
        other = self.application.create_intake(
            self.intake().model_copy(update={"source_event_id": "qa-other-task"}),
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN)
        self.assertEqual([], store.read_primary_qualification_evidence(other.task_id)["slack_deliveries"])
        # The snapshot's own projection is returned so callers derive the task
        # record from the same REPEATABLE READ view instead of a second read.
        self.assertEqual(record.task_id, document["projection"].task_id)
        self.assertEqual(document["projection_digest"], document["projection"].projection_digest)
        self.assertEqual({"isolation": "REPEATABLE READ", "read_only": True,
                          "single_transaction": True, "covers_task_record": True}, document["snapshot"])

    def test_qualification_readback_is_bounded_by_one_deadline_across_pool_wait_and_blocked_sql(self):
        import time as clock
        import psycopg
        from psycopg import sql
        from psycopg_pool import PoolTimeout
        from director_orchestrator.langgraph_postgres import (
            DeadlineExpired, bounded_connection, require_deadline_remaining)

        record = self.application.create_intake(
            self.intake(), intake_authentication=IntakeAuthentication.BEARER_DRY_RUN)
        store = self.coordinator._store
        # An already-spent bound is refused before any connection or row is touched.
        with self.assertRaises(DeadlineExpired):
            require_deadline_remaining(clock.monotonic() - 1, "probe")
        with self.assertRaises(DeadlineExpired):
            bounded_connection(self.handle.pool, clock.monotonic() - 1)
        # Blocked pool acquisition: every pooled connection is held elsewhere, so
        # the readback must give up within its bound instead of queueing.
        held = []
        try:
            while True:
                try:
                    manager = self.handle.pool.connection(timeout=0.5)
                    manager.__enter__()
                    held.append(manager)
                except PoolTimeout:
                    break
            started = clock.monotonic()
            with self.assertRaises(PoolTimeout):
                store.read_primary_qualification_evidence(record.task_id, wall_seconds=0.5)
            self.assertLess(clock.monotonic() - started, 2.0)
        finally:
            for manager in held:
                manager.__exit__(None, None, None)
        # Blocked SQL after earlier queries succeeded: a later table is locked by
        # another session, so the remaining time (not a fresh allowance) bounds
        # the wait and the whole read fails instead of finishing late.
        dsn = os.environ.get("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN", "").strip()
        with psycopg.connect(dsn, connect_timeout=5) as blocker:
            blocker.execute(sql.SQL("LOCK TABLE {}.director_primary_slack_delivery IN ACCESS EXCLUSIVE MODE")
                            .format(sql.Identifier(self.schema)))
            started = clock.monotonic()
            with self.assertRaises(psycopg.Error):
                store.read_primary_qualification_evidence(record.task_id, wall_seconds=0.6)
            self.assertLess(clock.monotonic() - started, 2.0)
            blocker.rollback()
        # QA control for the cumulative bound (a test gap closed here; no product
        # boundary changed): two ACCESS EXCLUSIVE locks, one on the projection
        # table the read touches first and one on the delivery table it touches
        # later, released in stages so that each wait alone fits the allowance
        # while their sum exceeds it. The real read must refuse before the later
        # lock is released; a per-stage allowance would instead finish late once
        # the second lock cleared. Both waits, the join and the cleanup are
        # bounded, and the blockers are released in `finally`.
        allowance, first_release, second_release = 4.0, 1.2, 4.8
        self.assertTrue(first_release < allowance and second_release - first_release < allowance < second_release)
        released, waiting, worker_errors = {}, {}, []
        stop, releaser = threading.Event(), None
        with (
            psycopg.connect(dsn, connect_timeout=5) as early,
            psycopg.connect(dsn, connect_timeout=5) as later,
            psycopg.connect(dsn, connect_timeout=5, autocommit=True,
                            options="-c statement_timeout=500") as observer,
        ):
            early.execute(sql.SQL("LOCK TABLE {}.director_primary_task_projection IN ACCESS EXCLUSIVE MODE")
                          .format(sql.Identifier(self.schema)))
            later.execute(sql.SQL("LOCK TABLE {}.director_primary_slack_delivery IN ACCESS EXCLUSIVE MODE")
                          .format(sql.Identifier(self.schema)))
            origin = clock.monotonic()

            def stage_releases():
                try:
                    for name, blocker, at, table in (
                        ("early", early, first_release, "director_primary_task_projection"),
                        ("later", later, second_release, "director_primary_slack_delivery"),
                    ):
                        # Observe the actual blocked reader, not just the release
                        # schedule: an unrelated database error must not pass.
                        while not stop.is_set() and clock.monotonic() < origin + at:
                            row = observer.execute(
                                "SELECT pid FROM pg_locks WHERE locktype='relation' "
                                "AND relation=%s::regclass AND NOT granted "
                                "AND %s=ANY(pg_blocking_pids(pid))",
                                (sql.Identifier(self.schema, table).as_string(observer),
                                 blocker.info.backend_pid),
                            ).fetchone()
                            if row is not None:
                                waiting[name] = row[0]
                                break
                            stop.wait(0.02)
                        if stop.wait(max(0.0, origin + at - clock.monotonic())):
                            return
                        blocker.rollback()
                        released[name] = clock.monotonic() - origin
                except Exception as error:
                    worker_errors.append(error)

            releaser = threading.Thread(target=stage_releases, name="qa-staged-lock-release", daemon=True)
            releaser.start()
            try:
                with self.assertRaises((psycopg.errors.QueryCanceled, psycopg.errors.LockNotAvailable,
                                        psycopg.errors.TransactionTimeout, DeadlineExpired)):
                    store.read_primary_qualification_evidence(record.task_id, wall_seconds=allowance)
                refused_at = clock.monotonic() - origin
                releaser.join(timeout=second_release + 5.0)
                self.assertFalse(releaser.is_alive())
                self.assertEqual([], worker_errors)
                self.assertEqual({"early", "later"}, set(waiting))
                self.assertEqual(waiting["early"], waiting["later"])
                self.assertLess(released["early"], refused_at)
                self.assertLess(refused_at, released["later"])
                self.assertLess(refused_at, allowance + 0.75)
            finally:
                stop.set()
                releaser.join(timeout=10.0)
                self.assertFalse(releaser.is_alive())
        # The lane is intact afterwards and a normal read still completes.
        after = store.read_primary_qualification_evidence(record.task_id)
        self.assertTrue(after["complete"])
        self.assertLess(after["bounds"]["elapsed_seconds"], after["bounds"]["wall_seconds"])

    def test_qualification_readback_joins_populated_attempts_receipts_and_deliveries_by_real_keys(self):
        from psycopg import sql
        from director_orchestrator.models import QualificationEvidenceReadback

        record = self.application.create_intake(
            self.intake(), intake_authentication=IntakeAuthentication.BEARER_DRY_RUN)
        store = self.coordinator._store
        base = store.read_primary_qualification_evidence(record.task_id)
        self.assertGreaterEqual(len(base["assignments"]), 1)
        assignment = base["assignments"][0]
        digest = lambda label: "sha256:" + hashlib.sha256(label.encode()).hexdigest()
        with self.handle.pool.connection() as connection:
            for event, status in ((1, "STARTED"), (2, "SUCCEEDED")):
                connection.execute(sql.SQL("""
                    INSERT INTO {}.director_primary_provider_attempt
                        (task_id, assignment_id, attempt_ordinal, event_sequence, provider_id, format_id,
                         transport_id, idempotency_key_digest, attempt_digest, provider_run_id_digest,
                         status, result_digest, outcome_evidence_digest, reason_code, budget_evidence_digest)
                    VALUES (%s, %s, 1, %s, %s, 'v1', 'synthetic-transport', %s, %s, %s, %s, %s, %s, %s, %s)
                    """).format(sql.Identifier(self.schema)),
                    (record.task_id, assignment["assignment_id"], event, assignment["provider_id"],
                     digest("idem:" + record.task_id), digest("attempt:" + record.task_id),
                     digest("run:" + record.task_id) if event == 2 else None, status,
                     digest("result:" + record.task_id) if event == 2 else None,
                     digest("outcome:" + record.task_id) if event == 2 else None,
                     "attempt_started" if event == 1 else "attempt_succeeded", digest("budget:" + record.task_id)))
            connection.execute(sql.SQL("""
                INSERT INTO {}.director_primary_slack_delivery
                    (task_id, notification_key, projection_digest, payload, client_msg_id, state)
                VALUES (%s, 'terminal', %s, '{{}}'::jsonb, %s, 'SUPPRESSED')
                """).format(sql.Identifier(self.schema)),
                (record.task_id, base["projection_digest"], "synthetic-delivery-" + record.task_id))
        # A producer-shaped approval delivery row through the real delivery
        # producer (PrimarySlackDelivery.enqueue builds the 152-character
        # approval:<request digest>:<projection digest> key and the uuid5
        # client_msg_id) and a real owner decision bound to a real command
        # receipt (record_owner_decision). Seam: the dry-run intake is not
        # gateway-attested, so the producer renders no payload and stores the
        # row SUPPRESSED; the key, identity and decision columns are the
        # producer's own. Slack transport is never constructed (transport=None,
        # no run_once).
        from director_orchestrator.primary_slack_delivery import PrimarySlackDelivery
        projection = self.coordinator.get_projection(record.task_id)
        pending = projection.projection["pending_approval"]
        approval_key = "approval:" + pending["request_digest"] + ":" + projection.projection_digest
        self.assertEqual(152, len(approval_key))
        delivery_settings = replace(self.settings, langgraph_primary_postgres_schema=self.schema,
                                    slack_channel_id="CDEFAULT", slack_owner_user_id="UOWNER",
                                    slack_bot_token="synthetic-slack-test-token")
        producer = PrimarySlackDelivery(SimpleNamespace(handle=self.handle, coordinator=self.coordinator,
                                                        application=self.application), delivery_settings,
                                        transport=None)
        self.coordinator.renew_new_intake()
        self.assertEqual(1, producer.enqueue())
        command = SimpleNamespace(task_id=record.task_id, decision="approve", command_id="qa-command-" + record.task_id,
                                  command_digest=digest("command:" + record.task_id),
                                  request_digest=pending["request_digest"],
                                  expected_projection_digest=projection.projection_digest)
        self.coordinator.record_command_receipt(task_id=record.task_id, operation="approval_resume",
                                                command_id=command.command_id, command_digest=command.command_digest,
                                                projection=projection)
        producer.record_owner_decision(command)
        produced = [row for row in producer.receipts(record.task_id) if row["notification_key"] == approval_key]
        self.assertEqual(1, len(produced))
        self.assertEqual(("SUPPRESSED", "approve", command.command_id),
                         (produced[0]["state"], produced[0]["owner_decision"], produced[0]["owner_command_id"]))
        self.assertIsNotNone(produced[0]["owner_decision_at"])
        approval_identity = produced[0]["client_msg_id"]
        receipt_id, created, _ = store.record_primary_slack_status_receipt(
            source_event_id="status-event-" + record.task_id, receipt_id="receipt-" + record.task_id,
            task_id=record.task_id, channel_id=record.intake.channel_id, thread_ts=record.intake.thread_ts,
            user_id=record.intake.user_id, text_digest=digest("status text"))
        self.assertTrue(created)  # the store reports creation; the API maps it to deduplicated=False
        # A real generic external effect through the production store producers
        # (claim -> reservation -> execution marker -> remote binding -> terminal
        # evidence), fenced by the task's actual writer lease.
        from director_orchestrator.langgraph_primary import ClaimRequest
        writer = store.current_task_writer(record.task_id)
        with self.handle.pool.connection() as connection:
            budget_row = connection.execute(sql.SQL(
                "SELECT budget_limit_units FROM {}.director_primary_budget WHERE task_id=%s"
            ).format(sql.Identifier(self.schema)), (record.task_id,)).fetchone()
        budget_limit = int(budget_row["budget_limit_units"]) if budget_row else 8
        store.register_task_budget(record.task_id, budget_limit_units=budget_limit)
        claim_key, effect_key = digest("effect-claim:" + record.task_id), digest("effect-key:" + record.task_id)
        effect_attempt, reservation_key = digest("effect-attempt:" + record.task_id), digest("effect-reservation:" + record.task_id)
        granted = store.claim(ClaimRequest(claim_key=claim_key, task_id=record.task_id, field="effect_status",
                                           expected_state="CLAIMED", target_state="EXECUTING",
                                           attempt_digest=effect_attempt, owner_token=writer.owner_token))
        self.assertTrue(granted.granted)
        store.reserve_effect_intent(reservation_key=reservation_key, claim_key=claim_key, task_id=record.task_id,
                                    owner_token=writer.owner_token, effect_key_digest=effect_key,
                                    effect_attempt_digest=effect_attempt, writer_owner_id=writer.engine_owner,
                                    reserved_units=1)
        self.assertTrue(store.claim_effect_execution(reservation_key=reservation_key, owner_token=writer.owner_token))
        store.bind_remote_effect(reservation_key=reservation_key, owner_token=writer.owner_token,
                                 remote_system="slack", remote_identifier="1787450010." + record.task_id[-6:].lower())
        store.record_terminal_evidence(claim_key=claim_key, owner_token=writer.owner_token, disposition="COMPLETED",
                                       evidence_digest=digest("terminal:" + record.task_id))
        # Authenticated spend/usage rows joined to the settled attempt digest. The
        # production meter mints these under a signed grant; the disposable lane has
        # no grant, so the rows are inserted synthetically under the real DDL/FKs.
        with self.handle.pool.connection() as connection:
            connection.execute(sql.SQL("""
                INSERT INTO {0}.director_primary_provider_budget (authority_digest, document)
                VALUES (%s, '{{"synthetic": true}}'::jsonb) ON CONFLICT DO NOTHING
                """).format(sql.Identifier(self.schema)), (digest("budget:" + record.task_id),))
            connection.execute(sql.SQL("""
                INSERT INTO {0}.director_primary_provider_spend
                    (attempt_digest, grant_digest, provider_id, project_id, task_id, budget_day, reserved_microusd, token_limit)
                VALUES (%s, %s, %s, 'synthetic-project', %s, CURRENT_DATE, 50000, 4000)
                """).format(sql.Identifier(self.schema)),
                (digest("attempt:" + record.task_id), digest("budget:" + record.task_id), assignment["provider_id"], record.task_id))
            connection.execute(sql.SQL("""
                INSERT INTO {0}.director_primary_provider_usage (attempt_digest, document, actual_microusd, observed_tokens)
                VALUES (%s, '{{"synthetic": true}}'::jsonb, 12500, 900)
                """).format(sql.Identifier(self.schema)), (digest("attempt:" + record.task_id),))
        document = store.read_primary_qualification_evidence(record.task_id)
        document.pop("projection")
        typed = QualificationEvidenceReadback.model_validate(document)  # strict typed serialization
        self.assertEqual(1, len(typed.effects))
        self.assertEqual((effect_key, effect_attempt, "COMPLETED"), (typed.effects[0].effect_key_digest,
                         typed.effects[0].effect_attempt_digest, typed.effects[0].terminal_disposition))
        self.assertEqual([], typed.absent)
        self.assertEqual([(digest("attempt:" + record.task_id), 12500, 900, 50000)],
                         [(row.attempt_digest, row.actual_microusd, row.observed_tokens, row.reserved_microusd)
                          for row in typed.provider_usage])
        attempts = typed.provider_attempts
        self.assertEqual([1, 2], [row.event_sequence for row in attempts])
        self.assertTrue(all(row.assignment_id == assignment["assignment_id"]
                            and row.assignment_sequence == assignment["sequence"]
                            and row.attempt_digest == digest("attempt:" + record.task_id)
                            and row.transport_id == "synthetic-transport" for row in attempts))
        self.assertEqual(["STARTED", "SUCCEEDED"], [row.status for row in attempts])
        self.assertEqual(digest("run:" + record.task_id), attempts[1].provider_run_id_digest)
        self.assertEqual(digest("result:" + record.task_id), attempts[1].result_digest)
        self.assertEqual([receipt_id], [row.receipt_id for row in typed.status_receipts])
        self.assertEqual("status-event-" + record.task_id, typed.status_receipts[0].source_event_id)
        self.assertEqual({"synthetic-delivery-" + record.task_id, approval_identity},
                         {row.client_msg_id for row in typed.slack_deliveries})
        # The full producer key survives the typed model and the API serialization.
        produced_row = next(row for row in typed.slack_deliveries if row.client_msg_id == approval_identity)
        self.assertEqual((approval_key, "approve", command.command_id),
                         (produced_row.notification_key, produced_row.owner_decision, produced_row.owner_command_id))
        self.assertEqual(1, typed.task_scope_counters.effect_reservations)
        self.assertEqual(1, typed.task_scope_counters.attempt_ordinals)
        self.assertTrue(typed.complete)
        # Rows are pinned to the task: another task sees none of them.
        other = self.application.create_intake(
            self.intake().model_copy(update={"source_event_id": "qa-populated-other"}),
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN)
        foreign = store.read_primary_qualification_evidence(other.task_id)
        self.assertEqual(([], [], []), (foreign["provider_attempts"], foreign["status_receipts"], foreign["slack_deliveries"]))
        self.assertEqual(2, typed.task_scope_counters.provider_attempts)
        self.assertEqual(1, typed.task_scope_counters.started_attempts)
        self.assertEqual(1, typed.task_scope_counters.status_receipts)
        self.assertGreaterEqual(typed.task_scope_counters.thread_tasks, 1)
        # Composition: the real serialized opt-in API response goes through the
        # collector and the strict observation schema (no handwritten shape).
        import importlib.util
        from fastapi.testclient import TestClient
        from director_orchestrator import main
        spec = importlib.util.spec_from_file_location(
            "offline_slack_trial_collector_composition",
            Path(__file__).resolve().parents[1] / "scripts/collect-offline-slack-trials.py")
        collector = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(collector)
        settings = replace(self.settings, shared_secret="standalone-qa-synthetic-secret",
                           langgraph_primary_new_intake_enabled=True)
        activation = SimpleNamespace(application=self.application, coordinator=self.coordinator)
        with patch.object(main, "settings", settings), \
             patch.object(main, "primary_default_activation", activation):
            client = TestClient(main.app)
            try:
                response = client.get(f"/v1/tasks/{record.task_id}",
                                      headers={"Authorization": "Bearer standalone-qa-synthetic-secret"},
                                      params={"include_qualification_evidence": "true"})
            finally:
                client.close()
        self.assertEqual(200, response.status_code, response.text)
        row = collector.summarize(record.intake.thread_ts, record.task_id, response.json(), [], [], True,
                                  record.intake.user_id, "UBOT", dict(response.headers), record.intake.channel_id)
        evidence = row["qualification_evidence"]
        self.assertEqual("collected", evidence["readback"]["state"], evidence["readback"]["reason"])
        self.assertEqual([digest("attempt:" + record.task_id)], evidence["chain"]["attempt_ids"])
        self.assertEqual(sorted(row["assignment_id"] for row in base["assignments"]), evidence["chain"]["assignment_ids"])
        self.assertIn(assignment["assignment_id"], evidence["chain"]["assignment_ids"])
        self.assertEqual([receipt_id], evidence["chain"]["status_receipt_ids"])
        self.assertEqual(sorted(["synthetic-delivery-" + record.task_id, approval_identity]), evidence["chain"]["delivery_ids"])
        # The 152-character approval key joins this task's snapshot as its own family.
        self.assertEqual([approval_key], evidence["chain"]["approval_delivery_keys"])
        self.assertEqual(response.json()["qualification_evidence"]["projection_digest"],
                         next(row for row in response.json()["qualification_evidence"]["slack_deliveries"]
                              if row["notification_key"] == approval_key)["projection_digest"])
        self.assertEqual([digest("result:" + record.task_id)], evidence["chain"]["provider_result_digests"])
        self.assertEqual("PROVIDER_RESULT_RECORDED", evidence["provider_effect_state"])
        self.assertEqual("EXTERNAL_EFFECT_TERMINAL:COMPLETED", evidence["external_effect_state"])
        self.assertEqual([effect_key], evidence["chain"]["effect_ids"])
        self.assertEqual("PROVIDER_EFFECT_RECORDED", evidence["effect_state"])
        self.assertEqual("synthetic-transport", evidence["lineage"]["transport"])
        self.assertEqual("authenticated_usage_table", evidence["usage"]["cost_basis"])
        self.assertEqual(0.0125, evidence["usage"]["actual_cost_usd"])
        self.assertEqual(50000, evidence["usage"]["actual_usage"]["reserved_budget_microusd"])
        self.assertEqual(1, evidence["status_receipts"]["durable_receipt_rows"])
        self.assertIsNone(evidence["status_receipts"]["deduplicated_receipts"])
        self.assertEqual(response.headers["X-Primary-Projection-Digest"], evidence["readback"]["projection_digest"])
        # Negative composition: a foreign task id in the same real response is refused;
        # a truncated real readback derives nothing.
        foreign_response = dict(response.json())
        foreign_response["qualification_evidence"] = {**foreign_response["qualification_evidence"], "task_id": other.task_id}
        refused = collector.summarize(record.intake.thread_ts, record.task_id, foreign_response, [], [], True,
                                      record.intake.user_id, "UBOT", dict(response.headers), record.intake.channel_id)
        self.assertEqual("rejected", refused["qualification_evidence"]["readback"]["state"])
        self.assertEqual([], refused["qualification_evidence"]["chain"]["attempt_ids"])
        truncated_document = store.read_primary_qualification_evidence(record.task_id, row_limit=1)
        truncated_document.pop("projection")
        # Serialized exactly as the API serializes the typed model (no str() datetimes).
        truncated_response = {**response.json(), "qualification_evidence":
                              QualificationEvidenceReadback.model_validate(truncated_document).model_dump(mode="json")}
        partial = collector.summarize(record.intake.thread_ts, record.task_id, truncated_response, [], [], True,
                                      record.intake.user_id, "UBOT", dict(response.headers), record.intake.channel_id)
        self.assertEqual("incomplete", partial["qualification_evidence"]["readback"]["state"])
        self.assertEqual("UNKNOWN", partial["qualification_evidence"]["usage"]["cost_basis"])
        self.assertIsNone(partial["qualification_evidence"]["effect_state"])
        # Neither a rejected nor a truncated real readback promotes any delivery,
        # key, route or sequence into the qualification chain.
        for derived in (refused["qualification_evidence"], partial["qualification_evidence"]):
            self.assertEqual(([], [], [], []), (derived["chain"]["delivery_ids"], derived["chain"]["approval_delivery_keys"],
                                                derived["chain"]["assignment_sequences"], derived["lineage"]["provider_routes"]))
            self.assertEqual("ordinary task-record/deliveries context; not the validated snapshot join",
                             derived["unjoined_context"]["note"])
        collector.validate_observations({"schema": "director-slack-trial-observations/v1",
            "observed_at": collector.utc(), "expires_at": collector.utc(), "requested_trials": 1, "observed_trials": 1,
            "rubric_sha256": None, "collection_runtime_before": {"container_id": "c", "image_id": "sha256:" + "a" * 64,
                "runtime_head": "b" * 40, "started_at": None, "offline_configured": True, "paid_configured": False},
            "collection_runtime_after": None, "runtime_collection_binding_stable": None,
            "runtime_health_at_collection": {"paid_runs_enabled": None, "ambiguous_effect_count": None,
                "reconciliation_hold_reason": None, "primary_runtime_active": None},
            "collector_sha256": None, "read_only": True, "slack_writes": 0, "provider_dispatches": 0,
            "scorecard_mutations": 0, "qualified_run_count": None, "ten_consecutive_runs_verified": False,
            "native_source_reads": [], "trials": [{**row, "input_rubric_sha256": None,
                "rubric_evaluation": collector.evaluate_rubric(row, {"declared_at": "2020-01-01T00:00:00+00:00"}, "0" * 64),
                "source_errors": [], "runtime_collection_binding_stable": None}], "limitations": []})

    def test_intake_503_after_committed_reservation_keeps_the_reserved_task(self):
        import psycopg
        import httpx
        from fastapi.testclient import TestClient
        from director_orchestrator import main

        def deny_http(*_args, **_kwargs):
            raise AssertionError("External HTTP is forbidden in this test")

        with patch.object(httpx.HTTPTransport, "handle_request", side_effect=deny_http), \
             patch.object(httpx.AsyncHTTPTransport, "handle_async_request", side_effect=deny_http):
            settings = replace(
                self.settings, shared_secret="standalone-qa-synthetic-secret",
                langgraph_primary_new_intake_enabled=True,
                langgraph_primary_live_provider_dispatch_enabled=False,
            )
            activation = SimpleNamespace(application=self.application)
            headers = {"Authorization": "Bearer standalone-qa-synthetic-secret"}
            captured: dict = {}

            def request(client, suffix):
                payload = self.intake().model_copy(update={"source_event_id": "qa-" + suffix})
                return client.post("/v1/intake", headers=headers, json=payload.model_dump(mode="json"))

            def fail_after_reservation(command):
                captured["task_id"] = command.task_id
                before_failure = self.fresh_witness(command.task_id)
                self.assertEqual(before_failure["projection_rows"], 1)
                self.assertEqual(before_failure["dedup_rows"], 1)
                captured["visible_before_failure"] = before_failure
                captured["failure_reached"] = True
                raise psycopg.OperationalError("QA injected failure after committed reservation")

            with patch.object(main, "settings", settings), \
                 patch.object(main, "primary_default_activation", activation):
                # Deliberately no context manager: no ASGI lifespan or service startup.
                client = TestClient(main.app)
                try:
                    with patch.object(self.coordinator._runtime, "start", side_effect=fail_after_reservation) as fault:
                        response = request(client, "after-reservation")
                    fault.assert_called_once()
                    witness = self.fresh_witness(captured["task_id"])

                    self.assertEqual(503, response.status_code)
                    self.assertEqual(1, witness.get("projection_rows"))
                    self.assertEqual(1, witness.get("dedup_rows"))
                    self.assertTrue(captured.get("failure_reached", False))
                    self.assertEqual("intake_reserved", witness.get("reason_code"))
                    self.assertEqual("NOT_STARTED", witness.get("provider_dispatch_status"))
                    self.assertEqual(response.json(), {"detail": "LangGraph primary intake is unavailable"})

                    # Same outward 503 without a reservation MUST fail the evidence predicate.
                    early = {}
                    def fail_before_reservation(**kwargs):
                        early["task_id"] = kwargs["task_id"]
                        raise psycopg.OperationalError("QA injected failure before reservation")
                    with patch.object(type(self.coordinator._store), "create_primary_task_projection", side_effect=fail_before_reservation) as early_fault:
                        early_response = request(client, "before-reservation")
                    early_fault.assert_called_once()
                    early_witness = self.fresh_witness(early["task_id"])
                    self.assertEqual(early_response.status_code, 503)
                    self.assertEqual(0, early_witness.get("projection_rows"))

                    # Removing the injected fault must take the existing paid-off success path.
                    success = request(client, "without-fault")
                    self.assertEqual(success.status_code, 202)
                    successful_witness = self.fresh_witness(success.json()["task_id"])
                    self.assertEqual(successful_witness["provider_dispatch_status"], "NOT_STARTED")
                finally:
                    client.close()


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
                    "SLACK_DIRECTOR_CHANNEL_ID": "CDEFAULT",
                    "SLACK_GATEWAY_SECRET": "g" * 40,
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
import time
from fastapi.testclient import TestClient
from director_orchestrator.main import app
from director_orchestrator.primary_commands import (
    CancellationRequest, RecoveryRequest, PrimaryIngressProof,
    build_primary_ingress_proof,
)

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
    def signed_request(model, path):
        nonce = ("n" if model is CancellationRequest else "r") * 32
        placeholder = PrimaryIngressProof(
            issued_at=int(time.time()), nonce=nonce, signature="0" * 64,
            channel_id="CDEFAULT",
        )
        request = model(**model_payload, gateway_proof=placeholder)
        proof = build_primary_ingress_proof(
            "g" * 40, "test-shared-secret", request, path=path,
            proof_field="gateway_proof", issued_at=placeholder.issued_at,
            nonce=nonce, channel_id="CDEFAULT",
        )
        return request.model_copy(update={"gateway_proof": proof}).model_dump(mode="json")
    model_payload = {**command_binding, "command_id": "cancel-default-api-1"}
    cancel_path = f"/v1/primary/tasks/{task_id}/cancel"
    cancelled = client.post(
        cancel_path,
        headers=headers,
        json=signed_request(CancellationRequest, cancel_path)
    )


    if cancelled.status_code != 200:
        raise RuntimeError(cancelled.text)
    model_payload = {
        **command_binding,
        "command_id": "recover-default-api-1",
        "expected_projection_digest": cancelled.json()["projection_digest"],
    }
    recover_path = f"/v1/primary/tasks/{task_id}/recover"
    recovered = client.post(
        recover_path,
        headers=headers,
        json=signed_request(RecoveryRequest, recover_path)
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
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:12 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:40 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 07:22 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 07:30 UTC | END]]

# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:19 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:14 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-04 | 23:51 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 01:02 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:22 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:37 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 07:16 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 04:06 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-10 | 18:17 UTC | END]]
# [[Gemini Antigravity | Gemini 3.1 Pro (High) | 2026-09-11 | 19:26 UTC | END]]
# [[Claude Code mechanical merge | Claude Opus 5 | 2026-09-12 | 02:56 UTC | END]]

# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:04 UTC | END]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-13 | 02:45 UTC | END]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-13 | 03:50 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 18:45 UTC | END]]

# [[OpenAI Codex | GPT-6 Astra | 2026-09-14 | 06:14 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:45 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 22:45 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 23:25 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-22 | 00:15 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-22 | 01:35 UTC | END]]
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 01:53 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 12:05 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 12:57 UTC | END]]
# [[Codex Director integration | GPT-6 Astra | 2026-09-22 | 01:54 UTC | END]]
# [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-22 | 16:58 UTC | END]]
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 17:22 UTC | END]]

# [[Grok | grok-4.6 | 2026-09-22 | 22:10 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:43 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 07:05 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:27 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 21:18 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:01 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:38 UTC | END]]
