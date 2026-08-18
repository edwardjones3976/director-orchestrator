from __future__ import annotations

import sqlite3
import json
import tempfile
import threading
import unittest
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from director_orchestrator.config import Settings
from director_orchestrator.oracle_merge import (
    OracleChangedFile,
    OracleCheckRun,
    OracleGitHubMerge,
    OracleMergePolicy,
    OracleMergeService,
    OracleMergeStatus,
    OracleMergeStore,
    OraclePullRequestSnapshot,
    OracleReviewArtifact,
    OracleReviewVerdict,
    authenticate_claude_review_artifact,
    authenticate_provider_review_artifact,
)
from director_orchestrator.models import (
    Assignment,
    AssignmentOutcome,
    ClaudeCallback,
    DirectorPlan,
    ProviderName,
    ProviderRun,
    RiskLevel,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)


SHA = "a" * 40
NEW_SHA = "b" * 40
BASE_SHA = "d" * 40
NEW_BASE_SHA = "e" * 40
REVIEW_SECRET = "test-only-oracle-review-secret"


class FakeGitHub:
    def __init__(
        self,
        snapshot: OraclePullRequestSnapshot,
        *,
        merge_error: bool = False,
        advance_base_on_merge: bool = False,
        responses: tuple[OraclePullRequestSnapshot | Exception, ...] = (),
    ):
        self.current = snapshot
        self.merge_error = merge_error
        self.advance_base_on_merge = advance_base_on_merge
        # Scripted reads are returned in order; afterwards every read observes the
        # steady state in `current`.
        self.responses = list(responses)
        self.snapshot_calls = 0
        self.merge_calls = 0
        self.ready_calls = 0

    def snapshot(self, repository: str, pull_number: int) -> OraclePullRequestSnapshot:
        self.snapshot_calls += 1
        if self.responses:
            scripted = self.responses.pop(0)
            if isinstance(scripted, Exception):
                raise scripted
            return scripted
        return self.current

    def merge(
        self, repository: str, pull_number: int, expected_sha: str, expected_base_sha: str
    ) -> OracleGitHubMerge:
        self.merge_calls += 1
        if self.merge_error:
            raise RuntimeError("branch protection refused the merge")
        current_base = NEW_BASE_SHA if self.advance_base_on_merge else self.current.base_sha
        if expected_sha != self.current.head_sha or expected_base_sha != current_base:
            return OracleGitHubMerge(merged=False, actor="test-merger")
        return OracleGitHubMerge(
            merged=True, merge_commit_sha="c" * 40, actor="test-merger"
        )

    def mark_ready(
        self, repository: str, pull_number: int, expected_sha: str, expected_base_sha: str
    ) -> bool:
        self.ready_calls += 1
        if expected_sha != self.current.head_sha or expected_base_sha != self.current.base_sha:
            return False
        self.current = self.current.model_copy(update={"draft": False})
        return True


class OracleMergeGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = str(Path(self.temporary.name) / "oracle.sqlite3")
        self.sleeps: list[float] = []
        self.settings = Settings(
            sqlite_path=self.database,
            oracle_automerge_enabled=True,
            oracle_automerge_kill_switch=False,
            oracle_automerge_project="edge-oracle",
            oracle_automerge_repository="edwardjones3976/edge_engine",
            oracle_automerge_base_ref="main",
            oracle_automerge_branch_prefix="oracle/",
            oracle_automerge_allowed_paths=("oracle/**", "docs/oracle/**", "tests/oracle/**"),
            oracle_automerge_required_checks=("ci", "security"),
            oracle_automerge_required_check_app="github-actions",
            oracle_automerge_review_secret=REVIEW_SECRET,
        )

    def artifact(self, **changes) -> OracleReviewArtifact:
        values = {
            "artifact_type": "edge_oracle_exact_sha_review/v1",
            "artifact_id": "review-artifact-1",
            "assignment_id": "assignment-1",
            "project": "edge-oracle",
            "repository": "edwardjones3976/edge_engine",
            "pull_number": 101,
            "base_ref": "main",
            "base_sha": BASE_SHA,
            "head_ref": "oracle/revival-phase-1",
            "head_sha": SHA,
            "implementation_provider": "cursor",
            "implementation_actor": "cursor-agent",
            "reviewer_provider": "claude",
            "review_run_id": "claude-review-1",
            "verdict": OracleReviewVerdict.ACCEPT,
            "summary": "Exact-SHA review accepted the bounded Oracle change.",
            "evidence": ["Tests and changed paths reviewed at the exact SHA."],
            "acceptance_checks": ["ci", "security"],
            "rollback_instructions": "Revert the merge commit and restore the prior main ref.",
            "reviewed_at": datetime(2026, 8, 16, tzinfo=timezone.utc),
            "signature": "0" * 64,
        }
        if "artifact_id" in changes and "review_run_id" not in changes:
            values["review_run_id"] = f"review-for-{changes['artifact_id']}"
        if "artifact_id" in changes and "assignment_id" not in changes:
            values["assignment_id"] = f"assignment-for-{changes['artifact_id']}"
        values.update(changes)
        return OracleReviewArtifact.model_validate(values).sign(REVIEW_SECRET)

    def snapshot(self, **changes) -> OraclePullRequestSnapshot:
        values = {
            "repository": "edwardjones3976/edge_engine",
            "pull_number": 101,
            "title": "Oracle research scaffold",
            "body": "Adds a research-only calibration component.",
            "state": "open",
            "draft": False,
            "mergeable": True,
            "merged": False,
            "base_ref": "main",
            "base_sha": BASE_SHA,
            "head_ref": "oracle/revival-phase-1",
            "head_sha": SHA,
            "head_repository": "edwardjones3976/edge_engine",
            "author_login": "cursor-agent",
            "changed_files": [
                OracleChangedFile(
                    filename="oracle/calibration.py",
                    status="added",
                    additions=2,
                    patch="@@ -0,0 +1,2 @@\n+research_only = True\n+funded_execution = False",
                )
            ],
            "checks": [
                OracleCheckRun(
                    run_id=1, name="ci", status="completed", conclusion="success",
                    head_sha=SHA, app_slug="github-actions"
                ),
                OracleCheckRun(
                    run_id=2,
                    name="security",
                    status="completed",
                    conclusion="success",
                    head_sha=SHA,
                    app_slug="github-actions",
                ),
            ],
            "blocking_review_threads": 0,
            "review_threads_complete": True,
        }
        values.update(changes)
        return OraclePullRequestSnapshot.model_validate(values)

    def service(
        self,
        github: FakeGitHub,
        settings: Settings | None = None,
        store: OracleMergeStore | None = None,
    ) -> OracleMergeService:
        selected = settings or self.settings
        return OracleMergeService(
            OracleMergePolicy(selected),
            store or OracleMergeStore(self.database),
            github,
            sleep=self.sleeps.append,
        )

    def callback_record(self, *, include_exact_shas: bool = True) -> TaskRecord:
        review_context = "Independently review the Oracle PR"
        if include_exact_shas:
            review_context += f" at base {BASE_SHA} and head {SHA}"
        acceptance = (
            [f"Confirm exact head {SHA} and base {BASE_SHA}."]
            if include_exact_shas
            else ["Confirm the supplied review scope."]
        )
        return TaskRecord(
            task_id="DIR-ORACLE",
            status=TaskStatus.REVIEWING,
            intake=SlackIntake(
                source_event_id="evt-oracle",
                channel_id="C-ORACLE",
                thread_ts="1.2",
                user_id="U-OWNER",
                text="Review the Oracle pull request.",
            ),
            plan=DirectorPlan(
                project="edge-oracle",
                normalized_outcome="Review and merge one bounded Oracle change.",
                risk_level=RiskLevel.MEDIUM,
                approval_required=False,
                max_hops=3,
                max_revisions=1,
                assignments=[
                    Assignment(
                        sequence=1,
                        agent=ProviderName.CURSOR,
                        task="Implement the bounded Oracle change.",
                        acceptance=["Create the isolated implementation."],
                        repository="edwardjones3976/edge_engine",
                        starting_ref="main",
                    ),
                    Assignment(
                        sequence=2,
                        agent=ProviderName.CLAUDE,
                        task=review_context,
                        acceptance=acceptance,
                        repository="edwardjones3976/edge_engine",
                        starting_ref="main",
                        depends_on=[1],
                    ),
                ],
                final_acceptance=["Exact-SHA review is recorded."],
            ),
            provider_runs=[ProviderRun(
                provider=ProviderName.CLAUDE,
                assignment_sequence=2,
                run_id="claude-review-callback",
                status="completed",
                result=self.callback().result,
                provider_authenticated=True,
            )],
            assignment_outcomes=[AssignmentOutcome(
                assignment_sequence=1,
                provider=ProviderName.CURSOR,
                run_id="cursor-implementation-run",
                result="Implementation completed on the isolated branch.",
                review_summary="OpenAI accepted the implementation evidence.",
            )],
        )

    def callback(self, **payload_changes) -> ClaudeCallback:
        payload = self.artifact().model_dump(mode="json", exclude={"signature"})
        payload.update({
            "assignment_id": "DIR-ORACLE:2",
            "review_run_id": "claude-review-callback",
        })
        payload.update(payload_changes)
        return ClaudeCallback(
            run_id="claude-review-callback",
            status="completed",
            result=json.dumps(payload),
        )

    def test_verified_callback_context_authenticates_artifact_locally(self):
        artifact = authenticate_claude_review_artifact(
            self.callback_record(), self.callback(), REVIEW_SECRET
        )
        self.assertIsNotNone(artifact)
        self.assertTrue(artifact.signature_is_valid(REVIEW_SECRET))

    def test_verified_cursor_run_authenticates_artifact_locally(self):
        payload = self.artifact(
            implementation_provider="claude",
            implementation_actor="claude-agent",
            reviewer_provider="cursor",
            review_run_id="cursor-review-run",
        ).model_dump(mode="json", exclude={"signature"})
        payload.update({"assignment_id": "DIR-CURSOR:2"})
        run = ProviderRun(
            provider=ProviderName.CURSOR,
            assignment_sequence=2,
            run_id="cursor-review-run",
            status="FINISHED",
            result=json.dumps(payload),
            provider_authenticated=True,
        )
        base_record = self.callback_record()
        record = base_record.model_copy(
            update={
                "task_id": "DIR-CURSOR",
                "plan": base_record.plan.model_copy(
                    update={
                        "assignments": [
                            base_record.plan.assignments[0].model_copy(
                                update={"agent": ProviderName.CLAUDE}
                            ),
                            base_record.plan.assignments[1].model_copy(
                                update={"agent": ProviderName.CURSOR}
                            ),
                        ]
                    }
                ),
                "provider_runs": [run],
                "assignment_outcomes": [
                    base_record.assignment_outcomes[0].model_copy(
                        update={"provider": ProviderName.CLAUDE}
                    )
                ],
            }
        )

        artifact = authenticate_provider_review_artifact(record, run, REVIEW_SECRET)

        self.assertIsNotNone(artifact)
        self.assertEqual(artifact.reviewer_provider, "cursor")
        self.assertTrue(artifact.signature_is_valid(REVIEW_SECRET))

    def test_manual_claude_handoff_is_classified_before_review_authentication(self):
        record = self.callback_record()
        for result in (
            "Bounded implementation-owner handoff.",
            json.dumps({"status": "complete", "evidence": ["read-only"]}),
        ):
            with self.subTest(result=result):
                run = record.provider_runs[0].model_copy(
                    update={"status": "FINISHED", "result": result}
                )
                self.assertIsNone(
                    authenticate_provider_review_artifact(record, run, REVIEW_SECRET)
                )

    def test_manual_claude_completion_cannot_authenticate_review_artifact(self):
        record = self.callback_record()
        run = record.provider_runs[0].model_copy(
            update={"status": "FINISHED", "provider_authenticated": False}
        )

        with self.assertRaisesRegex(ValueError, "provider-authenticated"):
            authenticate_provider_review_artifact(record, run, REVIEW_SECRET)

    def test_manual_cursor_completion_cannot_authenticate_review_artifact(self):
        payload = self.artifact(
            implementation_provider="claude",
            implementation_actor="claude-agent",
            reviewer_provider="cursor",
            review_run_id="cursor-review-run",
        ).model_dump(mode="json", exclude={"signature"})
        payload["assignment_id"] = "DIR-CURSOR:2"
        run = ProviderRun(
            provider=ProviderName.CURSOR,
            assignment_sequence=2,
            run_id="cursor-review-run",
            status="FINISHED",
            result=json.dumps(payload),
            provider_authenticated=False,
        )
        base_record = self.callback_record()
        record = base_record.model_copy(
            update={
                "task_id": "DIR-CURSOR",
                "plan": base_record.plan.model_copy(
                    update={
                        "assignments": [
                            base_record.plan.assignments[0].model_copy(
                                update={"agent": ProviderName.CLAUDE}
                            ),
                            base_record.plan.assignments[1].model_copy(
                                update={"agent": ProviderName.CURSOR}
                            ),
                        ]
                    }
                ),
                "provider_runs": [run],
                "assignment_outcomes": [
                    base_record.assignment_outcomes[0].model_copy(
                        update={"provider": ProviderName.CLAUDE}
                    )
                ],
            }
        )

        with self.assertRaisesRegex(ValueError, "provider-authenticated"):
            authenticate_provider_review_artifact(record, run, REVIEW_SECRET)

    def test_review_provider_must_match_authenticated_run(self):
        callback = self.callback(reviewer_provider="cursor")
        record = self.callback_record()
        record.provider_runs[0] = record.provider_runs[0].model_copy(
            update={"result": callback.result}
        )
        with self.assertRaisesRegex(ValueError, "provider does not match"):
            authenticate_claude_review_artifact(record, callback, REVIEW_SECRET)

    def test_reviewer_cannot_relabel_its_own_implementation(self):
        callback = self.callback(implementation_provider="claude")
        record = self.callback_record()
        record.provider_runs[0] = record.provider_runs[0].model_copy(
            update={"result": callback.result}
        )
        with self.assertRaisesRegex(ValueError, "accepted dependency"):
            authenticate_claude_review_artifact(record, callback, REVIEW_SECRET)

    def test_callback_cannot_supply_its_own_signature_or_omit_sha_binding(self):
        signed_callback = self.callback(signature="f" * 64)
        signed_record = self.callback_record()
        signed_record.provider_runs[0] = signed_record.provider_runs[0].model_copy(
            update={"result": signed_callback.result}
        )
        with self.assertRaisesRegex(ValueError, "must not supply"):
            authenticate_claude_review_artifact(
                signed_record, signed_callback, REVIEW_SECRET
            )
        with self.assertRaisesRegex(ValueError, "exact reviewed head SHA"):
            authenticate_claude_review_artifact(
                self.callback_record(include_exact_shas=False), self.callback(), REVIEW_SECRET
            )
        failed_callback = self.callback().model_copy(update={"status": "failed"})
        with self.assertRaisesRegex(ValueError, "completed Claude callback"):
            authenticate_claude_review_artifact(
                self.callback_record(), failed_callback, REVIEW_SECRET
            )

    def test_accepted_exact_sha_merges_and_records_append_only_audit(self):
        github = FakeGitHub(self.snapshot())
        store = OracleMergeStore(self.database)
        result = OracleMergeService(
            OracleMergePolicy(self.settings), store, github
        ).process(self.artifact())

        self.assertEqual(result.status, OracleMergeStatus.MERGED)
        self.assertEqual(result.merge_commit_sha, "c" * 40)
        self.assertEqual(result.actor, "test-merger")
        self.assertEqual(result.check_run_ids, [1, 2])
        self.assertEqual(result.changed_paths, ["oracle/calibration.py"])
        self.assertEqual(github.merge_calls, 1)
        self.assertEqual(
            [event["event_type"] for event in store.audit_events("review-artifact-1")],
            ["review_artifact_reserved", "merge_gate_completed"],
        )
        connection = sqlite3.connect(self.database)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM oracle_merge_audit")
        finally:
            connection.close()

    def test_cursor_can_independently_accept_claude_implementation(self):
        artifact = self.artifact(
            implementation_provider="claude",
            implementation_actor="claude-agent",
            reviewer_provider="cursor",
            review_run_id="cursor-review-1",
        )
        snapshot = self.snapshot(author_login="claude-agent")

        result = self.service(FakeGitHub(snapshot)).process(artifact)

        self.assertEqual(result.status, OracleMergeStatus.MERGED)

    def test_duplicate_delivery_returns_recorded_result_without_second_merge(self):
        github = FakeGitHub(self.snapshot())
        service = self.service(github)
        first = service.process(self.artifact())
        second = service.process(self.artifact())

        self.assertEqual(first, second)
        self.assertEqual(github.snapshot_calls, 1)
        self.assertEqual(github.merge_calls, 1)

    def test_artifact_identifier_cannot_be_reused_for_another_review(self):
        github = FakeGitHub(self.snapshot())
        service = self.service(github)
        service.process(self.artifact())
        with self.assertRaisesRegex(ValueError, "reused"):
            service.process(self.artifact(review_run_id="claude-review-2"))

    def test_review_run_cannot_be_reused_for_another_artifact(self):
        github = FakeGitHub(self.snapshot())
        service = self.service(github)
        service.process(self.artifact())
        with self.assertRaisesRegex(ValueError, "review run"):
            service.process(
                self.artifact(
                    artifact_id="another-artifact", assignment_id="assignment-2",
                    review_run_id="claude-review-1"
                )
            )

    def assert_rejected(self, *, artifact=None, snapshot=None, reason: str) -> None:
        github = FakeGitHub(snapshot or self.snapshot())
        result = self.service(github).process(artifact or self.artifact())
        self.assertNotEqual(result.status, OracleMergeStatus.MERGED)
        self.assertEqual(github.merge_calls, 0)
        self.assertTrue(
            any(reason in item for item in [*result.reasons, *result.serious_safety_reasons]),
            result.model_dump(),
        )

    def test_head_advance_after_acceptance_fails_closed(self):
        checks = [item.model_copy(update={"head_sha": NEW_SHA}) for item in self.snapshot().checks]
        self.assert_rejected(
            snapshot=self.snapshot(head_sha=NEW_SHA, checks=checks), reason="head SHA changed"
        )

    def test_base_advance_after_acceptance_fails_closed(self):
        self.assert_rejected(
            snapshot=self.snapshot(base_sha=NEW_BASE_SHA), reason="base SHA changed"
        )

    def test_base_advance_during_final_merge_validation_fails_closed(self):
        github = FakeGitHub(self.snapshot(), advance_base_on_merge=True)
        result = self.service(github).process(self.artifact())
        self.assertEqual(result.status, OracleMergeStatus.ERROR)
        self.assertIsNone(result.merge_commit_sha)

    def test_invalid_signature_stops_before_github_or_audit(self):
        github = FakeGitHub(self.snapshot())
        artifact = self.artifact().model_copy(update={"summary": "tampered"})
        with self.assertRaisesRegex(ValueError, "signature"):
            self.service(github).process(artifact)
        self.assertEqual(github.snapshot_calls, 0)
        connection = sqlite3.connect(self.database)
        try:
            count = connection.execute("SELECT COUNT(*) FROM oracle_merge_attempts").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 0)

    def test_disabled_gate_rejects_invalid_signature_before_reservation(self):
        github = FakeGitHub(self.snapshot())
        settings = replace(
            self.settings,
            oracle_automerge_enabled=False,
            oracle_automerge_kill_switch=True,
        )
        artifact = self.artifact().model_copy(update={"summary": "tampered"})

        with self.assertRaisesRegex(ValueError, "signature"):
            self.service(github, settings).process(artifact)

        self.assertEqual(github.snapshot_calls, 0)
        self.assertEqual(github.merge_calls, 0)
        connection = sqlite3.connect(self.database)
        try:
            attempts = connection.execute(
                "SELECT COUNT(*) FROM oracle_merge_attempts"
            ).fetchone()[0]
            audit_events = connection.execute(
                "SELECT COUNT(*) FROM oracle_merge_audit"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(attempts, 0)
        self.assertEqual(audit_events, 0)

    def test_policy_evaluate_reports_eligible_when_no_reasons(self):
        result = OracleMergePolicy(self.settings).evaluate(
            self.artifact(), self.snapshot()
        )

        self.assertEqual(result.status, OracleMergeStatus.ELIGIBLE)
        self.assertEqual(result.reasons, [])
        self.assertEqual(result.serious_safety_reasons, [])

    def test_implementation_author_cannot_self_accept(self):
        self.assert_rejected(
            artifact=self.artifact(implementation_provider="claude"), reason="own work"
        )

    def test_missing_failed_and_pending_required_checks_fail_closed(self):
        cases = (
            ([self.snapshot().checks[0]], "required check is missing"),
            (
                [
                    self.snapshot().checks[0],
                    self.snapshot().checks[1].model_copy(update={"conclusion": "failure"}),
                ],
                "not successful",
            ),
            (
                [
                    self.snapshot().checks[0],
                    self.snapshot().checks[1].model_copy(
                        update={"status": "in_progress", "conclusion": None}
                    ),
                ],
                "not successful",
            ),
        )
        for index, (checks, reason) in enumerate(cases):
            with self.subTest(index=index):
                artifact = self.artifact(artifact_id=f"checks-{index}")
                self.assert_rejected(
                    artifact=artifact, snapshot=self.snapshot(checks=checks), reason=reason
                )

    def test_blocking_review_thread_fails_closed(self):
        self.assert_rejected(
            snapshot=self.snapshot(blocking_review_threads=1), reason="review threads"
        )

    def test_incomplete_review_thread_enumeration_fails_closed(self):
        self.assert_rejected(
            snapshot=self.snapshot(review_threads_complete=False), reason="incomplete"
        )

    def test_unauthorized_path_fails_closed(self):
        self.assert_rejected(
            snapshot=self.snapshot(
                changed_files=[OracleChangedFile(filename="scheduler.py", status="modified")]
            ),
            reason="exceed",
        )

    def test_rename_cannot_move_an_out_of_scope_source_into_oracle(self):
        self.assert_rejected(
            snapshot=self.snapshot(changed_files=[OracleChangedFile(
                filename="oracle/moved.py", previous_filename="scheduler.py",
                status="renamed", patch="@@ -1 +1 @@\n-old = True\n+research = True"
            )]),
            reason="scheduler.py",
        )

    def test_missing_patch_fails_closed(self):
        self.assert_rejected(
            snapshot=self.snapshot(changed_files=[OracleChangedFile(
                filename="oracle/model.bin", status="added", additions=1, patch=None
            )]),
            reason="incomplete",
        )

    def test_required_check_producer_and_duplicates_fail_closed(self):
        spoofed = self.snapshot().checks[0].model_copy(update={"app_slug": "third-party"})
        self.assert_rejected(
            artifact=self.artifact(artifact_id="spoofed-check"),
            snapshot=self.snapshot(checks=[spoofed, self.snapshot().checks[1]]),
            reason="untrusted producer",
        )
        duplicated = [*self.snapshot().checks, self.snapshot().checks[0].model_copy(update={"run_id": 3})]
        self.assert_rejected(
            artifact=self.artifact(artifact_id="duplicate-check"),
            snapshot=self.snapshot(checks=duplicated),
            reason="ambiguous",
        )

    def test_fork_head_and_changes_requested_fail_closed(self):
        self.assert_rejected(
            snapshot=self.snapshot(head_repository="attacker/edge_engine"), reason="head repository"
        )
        self.assert_rejected(
            artifact=self.artifact(artifact_id="changes-requested"),
            snapshot=self.snapshot(review_decision="CHANGES_REQUESTED"),
            reason="request changes",
        )

    def test_assignment_authority_is_consumed_by_terminal_safety_stop(self):
        github = FakeGitHub(self.snapshot(title="Enable automated funded bet execution"))
        service = self.service(github)
        stopped = service.process(self.artifact())
        self.assertEqual(stopped.status, OracleMergeStatus.SERIOUS_SAFETY_STOP)
        with self.assertRaisesRegex(ValueError, "assignment"):
            service.process(self.artifact(
                artifact_id="later-accept", assignment_id="assignment-1",
                review_run_id="claude-review-2"
            ))

    def test_serious_safety_language_stops_merge(self):
        self.assert_rejected(
            snapshot=self.snapshot(title="Enable automated funded bet execution"),
            reason="funded wagering",
        )

    def test_material_deletion_is_a_serious_safety_stop(self):
        self.assert_rejected(
            snapshot=self.snapshot(changed_files=[OracleChangedFile(
                filename="oracle/archive.py", status="removed", deletions=4,
                patch="@@ -1,4 +0,0 @@\n-old = True"
            )]),
            reason="material deletion",
        )

    def test_repository_project_base_and_branch_mismatches_fail_closed(self):
        cases = (
            (self.artifact(project="other"), "project"),
            (self.artifact(repository="example/other"), "repository"),
            (self.artifact(base_ref="develop"), "base ref"),
            (self.artifact(head_ref="feature/not-oracle"), "prefix"),
        )
        for index, (artifact, reason) in enumerate(cases):
            artifact = artifact.model_copy(update={
                "artifact_id": f"scope-{index}",
                "assignment_id": f"scope-assignment-{index}",
                "review_run_id": f"scope-review-{index}",
            }).sign(REVIEW_SECRET)
            with self.subTest(index=index):
                self.assert_rejected(artifact=artifact, reason=reason)

    def test_conflict_fails_closed(self):
        self.assert_rejected(
            artifact=self.artifact(artifact_id="state-conflict"),
            snapshot=self.snapshot(mergeable=False),
            reason="mergeability",
        )

    def test_eligible_draft_is_marked_ready_then_revalidated_and_merged(self):
        github = FakeGitHub(self.snapshot(draft=True))

        result = self.service(github).process(self.artifact(artifact_id="draft-ready"))

        self.assertEqual(result.status, OracleMergeStatus.MERGED)
        self.assertEqual(github.ready_calls, 1)
        self.assertEqual(github.snapshot_calls, 2)
        self.assertEqual(github.merge_calls, 1)

    def test_draft_with_serious_safety_signal_stops_before_ready_transition(self):
        github = FakeGitHub(
            self.snapshot(draft=True, title="Enable automated funded bet execution")
        )

        result = self.service(github).process(
            self.artifact(artifact_id="unsafe-draft")
        )

        self.assertEqual(result.status, OracleMergeStatus.SERIOUS_SAFETY_STOP)
        self.assertEqual(github.ready_calls, 0)
        self.assertEqual(github.merge_calls, 0)

    def test_unresolved_mergeability_after_ready_is_polled_until_resolved(self):
        github = FakeGitHub(
            self.snapshot(),
            responses=(
                self.snapshot(draft=True),
                self.snapshot(mergeable=None),
                self.snapshot(mergeable=None),
                self.snapshot(),
            ),
        )

        result = self.service(github).process(self.artifact(artifact_id="poll-resolves"))

        self.assertEqual(result.status, OracleMergeStatus.MERGED)
        self.assertEqual(github.ready_calls, 1)
        self.assertEqual(github.snapshot_calls, 4)
        self.assertEqual(github.merge_calls, 1)
        self.assertEqual(self.sleeps, [1.0, 2.0])

    def test_unresolved_mergeability_without_a_draft_transition_is_polled(self):
        github = FakeGitHub(
            self.snapshot(),
            responses=(self.snapshot(mergeable=None), self.snapshot(mergeable=None)),
        )

        result = self.service(github).process(self.artifact(artifact_id="poll-open"))

        self.assertEqual(result.status, OracleMergeStatus.MERGED)
        self.assertEqual(github.ready_calls, 0)
        self.assertEqual(github.snapshot_calls, 3)
        self.assertEqual(github.merge_calls, 1)
        self.assertEqual(self.sleeps, [1.0])

    def test_repeatedly_unresolved_mergeability_stays_retryable_and_then_recovers(self):
        github = FakeGitHub(
            self.snapshot(mergeable=None), responses=(self.snapshot(draft=True),)
        )
        store = OracleMergeStore(self.database)
        artifact = self.artifact(artifact_id="poll-timeout")

        timed_out = self.service(github, store=store).process(artifact)

        self.assertEqual(timed_out.status, OracleMergeStatus.ERROR)
        self.assertIn("unresolved", timed_out.reasons[0])
        self.assertIn("retryable", timed_out.reasons[0])
        self.assertEqual(github.merge_calls, 0)
        self.assertEqual(github.snapshot_calls, 6)
        self.assertEqual(self.sleeps, [1.0, 2.0, 4.0, 8.0])
        connection = sqlite3.connect(self.database)
        try:
            attempt = connection.execute(
                "SELECT status, result_json FROM oracle_merge_attempts WHERE artifact_id = ?",
                ("poll-timeout",),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(attempt[0], "RECOVERABLE")
        self.assertIsNone(attempt[1])
        with self.assertRaisesRegex(ValueError, "assignment"):
            self.service(github).process(
                self.artifact(
                    artifact_id="poll-timeout-poacher",
                    assignment_id="assignment-for-poll-timeout",
                )
            )

        github.current = self.snapshot()
        recovered = self.service(github, store=store).process(artifact)

        self.assertEqual(recovered.status, OracleMergeStatus.MERGED)
        self.assertEqual(github.merge_calls, 1)
        self.assertEqual(
            [event["event_type"] for event in store.audit_events("poll-timeout")],
            [
                "review_artifact_reserved",
                "merge_gate_recoverable",
                "review_artifact_retried",
                "merge_gate_completed",
            ],
        )

    def test_explicit_conflict_during_mergeability_poll_is_terminal(self):
        github = FakeGitHub(
            self.snapshot(),
            responses=(
                self.snapshot(draft=True),
                self.snapshot(mergeable=None),
                self.snapshot(mergeable=False),
            ),
        )
        store = OracleMergeStore(self.database)
        artifact = self.artifact(artifact_id="poll-conflict")
        service = self.service(github, store=store)

        result = service.process(artifact)

        self.assertEqual(result.status, OracleMergeStatus.REJECTED)
        self.assertIn(OracleMergePolicy.UNRESOLVED_MERGEABILITY_REASON, result.reasons)
        self.assertEqual(github.merge_calls, 0)
        self.assertEqual(github.snapshot_calls, 3)
        self.assertEqual(self.sleeps, [1.0])
        self.assertEqual(service.process(artifact), result)
        self.assertEqual(github.snapshot_calls, 3)
        self.assertEqual(
            [event["event_type"] for event in store.audit_events("poll-conflict")],
            ["review_artifact_reserved", "merge_gate_completed"],
        )

    def test_drift_while_mergeability_is_unresolved_is_terminal(self):
        drifted_checks = [
            check.model_copy(update={"head_sha": NEW_SHA})
            for check in self.snapshot().checks
        ]
        cases = (
            (
                self.snapshot(mergeable=None, head_sha=NEW_SHA, checks=drifted_checks),
                "head SHA changed",
            ),
            (self.snapshot(mergeable=None, base_sha=NEW_BASE_SHA), "base SHA changed"),
            (self.snapshot(mergeable=None, state="closed"), "not open and unmerged"),
            (self.snapshot(mergeable=None, merged=True), "not open and unmerged"),
            (
                self.snapshot(mergeable=None, repository="attacker/edge_engine"),
                "repository does not match",
            ),
            (self.snapshot(mergeable=None, pull_number=999), "pull request number"),
            (
                self.snapshot(mergeable=None, title="Enable automated funded bet execution"),
                "funded wagering",
            ),
        )
        for index, (drifted, reason) in enumerate(cases):
            with self.subTest(index=index):
                github = FakeGitHub(
                    self.snapshot(),
                    responses=(
                        self.snapshot(draft=True),
                        self.snapshot(mergeable=None),
                        drifted,
                    ),
                )

                result = self.service(github).process(
                    self.artifact(artifact_id=f"poll-drift-{index}")
                )

                self.assertNotEqual(result.status, OracleMergeStatus.MERGED)
                self.assertEqual(github.merge_calls, 0)
                self.assertEqual(github.snapshot_calls, 3)
                self.assertTrue(
                    any(
                        reason in item
                        for item in [*result.reasons, *result.serious_safety_reasons]
                    ),
                    result.model_dump(),
                )

    def released_attempt(self, artifact_id: str) -> tuple[str, str | None]:
        connection = sqlite3.connect(self.database)
        try:
            return connection.execute(
                "SELECT status, result_json FROM oracle_merge_attempts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        finally:
            connection.close()

    def test_flip_back_to_draft_without_a_gate_transition_is_terminal(self):
        # The gate never transitioned this pull request, so a draft flag appearing
        # under it is genuine drift rather than a lagging replica read.
        github = FakeGitHub(
            self.snapshot(),
            responses=(
                self.snapshot(mergeable=None),
                self.snapshot(mergeable=None, draft=True),
            ),
        )

        result = self.service(github).process(self.artifact(artifact_id="draft-flip"))

        self.assertEqual(result.status, OracleMergeStatus.REJECTED)
        self.assertIn(OracleMergePolicy.DRAFT_NOT_MERGEABLE_REASON, result.reasons)
        self.assertEqual(github.ready_calls, 0)
        self.assertEqual(github.merge_calls, 0)
        self.assertIsNotNone(self.released_attempt("draft-flip")[1])

    def test_stale_draft_flag_after_a_confirmed_transition_is_transient(self):
        # mark_ready confirmed isDraft=false through GraphQL, so a REST replica still
        # reporting draft=true is stale state, not a verdict.
        github = FakeGitHub(
            self.snapshot(),
            responses=(
                self.snapshot(draft=True),
                self.snapshot(draft=True, mergeable=None),
                self.snapshot(draft=True),
                self.snapshot(),
            ),
        )

        result = self.service(github).process(self.artifact(artifact_id="stale-draft"))

        self.assertEqual(result.status, OracleMergeStatus.MERGED)
        self.assertEqual(github.ready_calls, 1)
        self.assertEqual(github.merge_calls, 1)
        self.assertEqual(github.snapshot_calls, 4)
        self.assertEqual(self.sleeps, [1.0, 2.0])

    def test_persistent_draft_flag_is_recoverable_and_a_later_delivery_merges(self):
        github = FakeGitHub(
            self.snapshot(),
            responses=(
                self.snapshot(draft=True),
                self.snapshot(draft=True),
                self.snapshot(draft=True),
                self.snapshot(draft=True),
            ),
        )
        artifact = self.artifact(artifact_id="stuck-draft")

        result = self.service(github).process(artifact)

        self.assertEqual(result.status, OracleMergeStatus.ERROR)
        self.assertIn("still reported the pull request as draft", result.reasons[0])
        self.assertIn("remains retryable", result.reasons[0])
        self.assertEqual(github.merge_calls, 0)
        self.assertEqual(github.snapshot_calls, 4)
        self.assertEqual(self.sleeps, [1.0, 2.0])
        # Released, never consumed: the unresolved allowance is untouched and a later
        # delivery of the identical artifact still merges.
        self.assertEqual(self.released_attempt("stuck-draft"), ("RECOVERABLE", None))
        self.assertEqual(
            self.service(FakeGitHub(self.snapshot())).process(artifact).status,
            OracleMergeStatus.MERGED,
        )

    def test_unresolved_mergeability_does_not_veto_the_draft_transition(self):
        # A draft pull request whose first read has not resolved mergeability is the
        # ordinary case. Rejecting it before mark_ready consumed the artifact without
        # ever starting the bounded wait.
        github = FakeGitHub(
            self.snapshot(),
            responses=(
                self.snapshot(draft=True, mergeable=None),
                self.snapshot(mergeable=None),
                self.snapshot(),
            ),
        )

        result = self.service(github).process(self.artifact(artifact_id="draft-null"))

        self.assertEqual(result.status, OracleMergeStatus.MERGED)
        self.assertEqual(github.ready_calls, 1)
        self.assertEqual(github.merge_calls, 1)
        self.assertEqual(github.snapshot_calls, 3)
        self.assertEqual(self.sleeps, [1.0])

    def test_recovered_first_read_of_an_unresolved_draft_still_transitions(self):
        # Initial read raises, the retry returns draft=true with mergeable=null, and
        # the delivery still reaches the bounded wait and merges.
        github = FakeGitHub(
            self.snapshot(),
            responses=(
                TimeoutError("transient 504"),
                self.snapshot(draft=True, mergeable=None),
                self.snapshot(),
            ),
        )

        result = self.service(github).process(self.artifact(artifact_id="recovered-draft"))

        self.assertEqual(result.status, OracleMergeStatus.MERGED)
        self.assertEqual(github.ready_calls, 1)
        self.assertEqual(github.snapshot_calls, 3)
        # One sleep for the initial-read retry; the first post-ready read resolves
        # immediately, so the bounded wait never sleeps.
        self.assertEqual(self.sleeps, [1.0])

    def test_unresolved_draft_that_never_resolves_is_recoverable_not_terminal(self):
        github = FakeGitHub(
            self.snapshot(mergeable=None),
            responses=(self.snapshot(draft=True, mergeable=None),),
        )
        artifact = self.artifact(artifact_id="draft-null-stuck")

        result = self.service(github).process(artifact)

        self.assertEqual(result.status, OracleMergeStatus.ERROR)
        self.assertIn("unresolved", result.reasons[0])
        self.assertIn("remains retryable", result.reasons[0])
        self.assertEqual(github.ready_calls, 1)
        self.assertEqual(github.merge_calls, 0)
        self.assertEqual(self.released_attempt("draft-null-stuck"), ("RECOVERABLE", None))
        self.assertEqual(
            self.service(FakeGitHub(self.snapshot())).process(artifact).status,
            OracleMergeStatus.MERGED,
        )

    def test_draft_preflight_still_blocks_on_every_other_condition(self):
        # Only unresolved mergeability is forgiven before the transition. An explicit
        # conflict and every other gate still stop the gate before mark_ready.
        cases = (
            ({"mergeable": False}, "conflicting or mergeability is unresolved"),
            ({"head_sha": NEW_SHA}, "head SHA changed"),
            ({"head_repository": "attacker/edge_engine"}, "head repository"),
            (
                {
                    "mergeable": None,
                    "changed_files": [
                        OracleChangedFile(
                            filename="scheduler.py",
                            status="modified",
                            patch="@@ -1 +1 @@\n+x = 1",
                        )
                    ],
                },
                "exceed",
            ),
            (
                {"mergeable": None, "title": "Enable automated funded bet execution"},
                "funded wagering",
            ),
        )
        for index, (change, reason) in enumerate(cases):
            with self.subTest(change=change):
                github = FakeGitHub(
                    self.snapshot(),
                    responses=(self.snapshot(draft=True, **change), self.snapshot()),
                )

                result = self.service(github).process(
                    self.artifact(artifact_id=f"preflight-block-{index}")
                )

                self.assertNotEqual(result.status, OracleMergeStatus.MERGED)
                self.assertEqual(github.ready_calls, 0)
                self.assertEqual(github.merge_calls, 0)
                self.assertEqual(github.snapshot_calls, 1)
                self.assertTrue(
                    any(
                        reason in item
                        for item in [*result.reasons, *result.serious_safety_reasons]
                    ),
                    result.model_dump(),
                )

    def test_first_read_failure_recovers_within_the_same_delivery(self):
        github = FakeGitHub(
            self.snapshot(), responses=(TimeoutError("transient 504"), self.snapshot())
        )

        result = self.service(github).process(self.artifact(artifact_id="first-read"))

        self.assertEqual(result.status, OracleMergeStatus.MERGED)
        self.assertEqual(github.snapshot_calls, 2)
        self.assertEqual(self.sleeps, [1.0])

    def test_exhausted_first_read_failures_are_recoverable_not_terminal(self):
        github = FakeGitHub(
            self.snapshot(),
            responses=(TimeoutError("transient 504"), ConnectionError("reset")),
        )
        artifact = self.artifact(artifact_id="first-read-exhausted")

        result = self.service(github).process(artifact)

        self.assertEqual(result.status, OracleMergeStatus.ERROR)
        self.assertIn("GitHub validation read failed closed", result.reasons[0])
        self.assertIn("remains retryable", result.reasons[0])
        self.assertEqual(github.merge_calls, 0)
        self.assertEqual(github.snapshot_calls, 2)
        self.assertEqual(self.sleeps, [1.0])
        self.assertEqual(
            self.released_attempt("first-read-exhausted"), ("RECOVERABLE", None)
        )
        # The reservation is held, so no other artifact can claim the assignment.
        with self.assertRaisesRegex(ValueError, "assignment"):
            self.service(FakeGitHub(self.snapshot())).process(
                self.artifact(
                    artifact_id="first-read-poacher",
                    assignment_id="assignment-for-first-read-exhausted",
                    review_run_id="first-read-poacher-run",
                )
            )
        self.assertEqual(
            self.service(FakeGitHub(self.snapshot())).process(artifact).status,
            OracleMergeStatus.MERGED,
        )

    def test_explicit_conflict_is_terminal_on_the_read_that_reports_it(self):
        # The transient classification is taken from the snapshot, so `false` can never
        # be forgiven as an unresolved `null`.
        github = FakeGitHub(
            self.snapshot(),
            responses=(
                self.snapshot(draft=True),
                self.snapshot(mergeable=None),
                self.snapshot(mergeable=False),
                self.snapshot(),
            ),
        )

        result = self.service(github).process(self.artifact(artifact_id="hard-conflict"))

        self.assertEqual(result.status, OracleMergeStatus.REJECTED)
        self.assertIn(
            OracleMergePolicy.UNRESOLVED_MERGEABILITY_REASON, result.reasons
        )
        self.assertEqual(github.snapshot_calls, 3)
        self.assertEqual(github.merge_calls, 0)
        self.assertIsNotNone(self.released_attempt("hard-conflict")[1])

    def test_stale_draft_allowance_is_independent_of_the_unresolved_allowance(self):
        # Two stale-draft reads and four unresolved reads both fit inside one delivery.
        # Under a shared budget the fifth unresolved read would have been refused, so
        # this proves neither allowance spends the other's.
        github = FakeGitHub(
            self.snapshot(),
            responses=(
                self.snapshot(draft=True),
                self.snapshot(draft=True),
                self.snapshot(draft=True),
                *(self.snapshot(mergeable=None) for _ in range(4)),
                self.snapshot(),
            ),
        )

        result = self.service(github).process(self.artifact(artifact_id="independent"))

        self.assertEqual(result.status, OracleMergeStatus.MERGED)
        self.assertEqual(github.snapshot_calls, 8)
        self.assertEqual(self.sleeps, [1.0, 2.0, 4.0, 8.0, 8.0, 8.0])

    def test_read_failure_between_unresolved_reads_still_reaches_the_resolved_snapshot(self):
        github = FakeGitHub(
            self.snapshot(),
            responses=(
                self.snapshot(draft=True),
                self.snapshot(mergeable=None),
                self.snapshot(mergeable=None),
                TimeoutError("GitHub read timed out"),
                self.snapshot(),
            ),
        )

        result = self.service(github).process(self.artifact(artifact_id="poll-mixed"))

        # The failed read spends only the read-failure allowance, so the two earlier
        # unresolved reads do not starve the poll and the resolved snapshot is reached.
        self.assertEqual(result.status, OracleMergeStatus.MERGED)
        self.assertEqual(github.ready_calls, 1)
        self.assertEqual(github.snapshot_calls, 5)
        self.assertEqual(github.merge_calls, 1)
        self.assertEqual(self.sleeps, [1.0, 2.0, 4.0])

    def test_first_poll_read_failure_recovers_within_the_same_delivery(self):
        github = FakeGitHub(
            self.snapshot(),
            responses=(
                self.snapshot(mergeable=None),
                ConnectionError("GitHub connection reset"),
            ),
        )
        store = OracleMergeStore(self.database)
        artifact = self.artifact(artifact_id="poll-first-read-failure")

        result = self.service(github, store=store).process(artifact)

        self.assertEqual(result.status, OracleMergeStatus.MERGED)
        self.assertEqual(github.snapshot_calls, 3)
        self.assertEqual(github.merge_calls, 1)
        self.assertEqual(self.sleeps, [1.0])
        self.assertEqual(
            [
                event["event_type"]
                for event in store.audit_events("poll-first-read-failure")
            ],
            ["review_artifact_reserved", "merge_gate_completed"],
        )

    def test_unresolved_allowance_is_exhausted_independently_of_read_failures(self):
        github = FakeGitHub(
            self.snapshot(mergeable=None),
            responses=(
                self.snapshot(mergeable=None),
                TimeoutError("GitHub read timed out"),
            ),
        )
        store = OracleMergeStore(self.database)
        artifact = self.artifact(artifact_id="poll-unresolved-exhausted")

        timed_out = self.service(github, store=store).process(artifact)

        self.assertEqual(timed_out.status, OracleMergeStatus.ERROR)
        self.assertIn("unresolved after 5", timed_out.reasons[0])
        self.assertIn("retryable", timed_out.reasons[0])
        self.assertEqual(github.merge_calls, 0)
        # One process read, one failed read, then the full five unresolved reads.
        self.assertEqual(github.snapshot_calls, 7)
        self.assertEqual(self.sleeps, [1.0, 2.0, 4.0, 8.0, 8.0])
        connection = sqlite3.connect(self.database)
        try:
            attempt = connection.execute(
                "SELECT status, result_json FROM oracle_merge_attempts WHERE artifact_id = ?",
                ("poll-unresolved-exhausted",),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(attempt[0], "RECOVERABLE")
        self.assertIsNone(attempt[1])

        github.current = self.snapshot()
        recovered = self.service(github, store=store).process(artifact)

        self.assertEqual(recovered.status, OracleMergeStatus.MERGED)
        self.assertEqual(github.merge_calls, 1)

    def test_recoverable_rearm_is_exclusive_across_store_instances(self):
        github = FakeGitHub(
            self.snapshot(mergeable=None), responses=(self.snapshot(draft=True),)
        )
        artifact = self.artifact(artifact_id="recoverable-rearm-race")
        store = OracleMergeStore(self.database)
        timed_out = self.service(github, store=store).process(artifact)
        self.assertEqual(timed_out.status, OracleMergeStatus.ERROR)

        barrier = threading.Barrier(2)

        class BarrierCursor:
            def __init__(self, cursor):
                self.cursor = cursor

            def fetchone(self):
                row = self.cursor.fetchone()
                if row and row["status"] == "RECOVERABLE":
                    barrier.wait(timeout=5)
                return row

            def __getattr__(self, name):
                return getattr(self.cursor, name)

        class BarrierConnection:
            def __init__(self, connection):
                self.connection = connection

            def execute(self, sql, parameters=()):
                cursor = self.connection.execute(sql, parameters)
                if "SELECT artifact_digest, status, result_json" in sql:
                    return BarrierCursor(cursor)
                return cursor

            def __getattr__(self, name):
                return getattr(self.connection, name)

        class BarrierStore(OracleMergeStore):
            @contextmanager
            def _connect(self):
                with super()._connect() as connection:
                    yield BarrierConnection(connection)

        outcomes = []
        errors = []

        def reserve_once(candidate_store):
            try:
                outcomes.append(candidate_store.reserve(artifact))
            except Exception as exc:  # the losing contender must fail closed
                errors.append(exc)

        contenders = [BarrierStore(self.database), BarrierStore(self.database)]
        threads = [
            threading.Thread(target=reserve_once, args=(candidate,))
            for candidate in contenders
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(outcomes, [None])
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], RuntimeError)
        self.assertIn("already being evaluated", str(errors[0]))
        self.assertEqual(
            [
                event["event_type"]
                for event in store.audit_events("recoverable-rearm-race")
            ].count("review_artifact_retried"),
            1,
        )

    def test_drift_after_a_read_failure_is_terminal(self):
        drifted_checks = [
            check.model_copy(update={"head_sha": NEW_SHA})
            for check in self.snapshot().checks
        ]
        github = FakeGitHub(
            self.snapshot(),
            responses=(
                self.snapshot(draft=True),
                TimeoutError("GitHub read timed out"),
                self.snapshot(mergeable=None, head_sha=NEW_SHA, checks=drifted_checks),
            ),
        )
        store = OracleMergeStore(self.database)
        artifact = self.artifact(artifact_id="poll-failure-then-drift")
        service = self.service(github, store=store)

        result = service.process(artifact)

        self.assertEqual(result.status, OracleMergeStatus.REJECTED)
        self.assertTrue(
            any("head SHA changed" in reason for reason in result.reasons),
            result.model_dump(),
        )
        self.assertEqual(github.merge_calls, 0)
        self.assertEqual(github.snapshot_calls, 3)
        self.assertEqual(self.sleeps, [1.0])
        # Drift observed after a recovered read failure is terminal, so the artifact is
        # consumed and redelivery replays the recorded result without calling GitHub.
        self.assertEqual(service.process(artifact), result)
        self.assertEqual(github.snapshot_calls, 3)
        self.assertEqual(
            [
                event["event_type"]
                for event in store.audit_events("poll-failure-then-drift")
            ],
            ["review_artifact_reserved", "merge_gate_completed"],
        )

    def test_poll_allowances_must_be_positive(self):
        store = OracleMergeStore(self.database)
        policy = OracleMergePolicy(self.settings)
        for bounds in (
            {"mergeable_poll_attempts": 0},
            {"mergeable_poll_read_failures": 0},
            {"mergeable_poll_stale_draft_reads": 0},
        ):
            with self.subTest(bounds=bounds):
                with self.assertRaisesRegex(ValueError, "at least one"):
                    OracleMergeService(
                        policy, store, FakeGitHub(self.snapshot()), **bounds
                    )

    def test_exhausted_snapshot_failures_are_retryable_and_a_later_delivery_merges(self):
        github = FakeGitHub(
            self.snapshot(),
            responses=(
                self.snapshot(draft=True),
                RuntimeError("GitHub secondary rate limit"),
                TimeoutError("GitHub read timed out"),
            ),
        )
        store = OracleMergeStore(self.database)
        artifact = self.artifact(artifact_id="poll-read-failure")

        failed = self.service(github, store=store).process(artifact)

        self.assertEqual(failed.status, OracleMergeStatus.ERROR)
        self.assertIn("re-read failed closed", failed.reasons[0])
        self.assertIn("retryable", failed.reasons[0])
        self.assertIn("TimeoutError", failed.reasons[0])
        self.assertEqual(github.merge_calls, 0)
        # Two failed reads spend the read-failure allowance and nothing else; the
        # unresolved-mergeability allowance is never touched.
        self.assertEqual(github.snapshot_calls, 3)
        self.assertEqual(self.sleeps, [1.0])
        connection = sqlite3.connect(self.database)
        try:
            attempt = connection.execute(
                "SELECT status, result_json FROM oracle_merge_attempts WHERE artifact_id = ?",
                ("poll-read-failure",),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(attempt[0], "RECOVERABLE")
        self.assertIsNone(attempt[1])
        with self.assertRaisesRegex(ValueError, "assignment"):
            self.service(github).process(
                self.artifact(
                    artifact_id="poll-read-failure-poacher",
                    assignment_id="assignment-for-poll-read-failure",
                )
            )

        recovered = self.service(github, store=store).process(artifact)

        self.assertEqual(recovered.status, OracleMergeStatus.MERGED)
        self.assertEqual(github.ready_calls, 1)
        self.assertEqual(github.merge_calls, 1)

        duplicate = self.service(github, store=store).process(artifact)

        self.assertEqual(duplicate, recovered)
        self.assertEqual(github.merge_calls, 1)
        self.assertEqual(
            [event["event_type"] for event in store.audit_events("poll-read-failure")],
            [
                "review_artifact_reserved",
                "merge_gate_recoverable",
                "review_artifact_retried",
                "merge_gate_completed",
            ],
        )

    def test_ambiguous_merge_call_is_never_retried(self):
        github = FakeGitHub(self.snapshot(), merge_error=True)
        store = OracleMergeStore(self.database)
        artifact = self.artifact(artifact_id="merge-ambiguous")

        failed = self.service(github, store=store).process(artifact)

        self.assertEqual(failed.status, OracleMergeStatus.ERROR)
        self.assertIn("GitHub merge failed closed", failed.reasons[0])
        self.assertEqual(github.merge_calls, 1)

        duplicate = self.service(github, store=store).process(artifact)

        self.assertEqual(duplicate, failed)
        self.assertEqual(github.merge_calls, 1)
        self.assertEqual(
            [event["event_type"] for event in store.audit_events("merge-ambiguous")],
            ["review_artifact_reserved", "merge_gate_completed"],
        )

    def test_a_terminal_attempt_cannot_be_released_for_retry(self):
        github = FakeGitHub(self.snapshot())
        store = OracleMergeStore(self.database)
        merged = self.service(github, store=store).process(
            self.artifact(artifact_id="terminal-release")
        )

        self.assertEqual(merged.status, OracleMergeStatus.MERGED)
        with self.assertRaisesRegex(RuntimeError, "unresolved merge attempt"):
            store.release(merged)

    def test_branch_protection_or_github_refusal_is_recorded_as_error(self):
        github = FakeGitHub(self.snapshot(), merge_error=True)
        result = self.service(github).process(self.artifact())
        self.assertEqual(result.status, OracleMergeStatus.ERROR)
        self.assertIn("failed closed", result.reasons[0])

    def test_kill_switch_stops_before_any_github_call(self):
        github = FakeGitHub(self.snapshot())
        settings = replace(self.settings, oracle_automerge_kill_switch=True)
        result = self.service(github, settings).process(self.artifact())
        self.assertEqual(result.status, OracleMergeStatus.REJECTED)
        self.assertEqual(github.snapshot_calls, 0)
        self.assertEqual(github.merge_calls, 0)

    def test_feature_flag_defaults_disabled_and_killed(self):
        settings = Settings()
        self.assertFalse(settings.oracle_automerge_enabled)
        self.assertTrue(settings.oracle_automerge_kill_switch)


if __name__ == "__main__":
    unittest.main()
