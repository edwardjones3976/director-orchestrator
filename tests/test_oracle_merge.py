from __future__ import annotations

import sqlite3
import json
import tempfile
import unittest
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
)
from director_orchestrator.models import (
    Assignment,
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
    ):
        self.current = snapshot
        self.merge_error = merge_error
        self.advance_base_on_merge = advance_base_on_merge
        self.snapshot_calls = 0
        self.merge_calls = 0

    def snapshot(self, repository: str, pull_number: int) -> OraclePullRequestSnapshot:
        self.snapshot_calls += 1
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


class OracleMergeGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = str(Path(self.temporary.name) / "oracle.sqlite3")
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

    def service(self, github: FakeGitHub, settings: Settings | None = None) -> OracleMergeService:
        selected = settings or self.settings
        return OracleMergeService(
            OracleMergePolicy(selected), OracleMergeStore(self.database), github
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
                assignments=[Assignment(
                    sequence=2,
                    agent=ProviderName.CLAUDE,
                    task=review_context,
                    acceptance=acceptance,
                    repository="edwardjones3976/edge_engine",
                    starting_ref="main",
                )],
                final_acceptance=["Exact-SHA review is recorded."],
            ),
            provider_runs=[ProviderRun(
                provider=ProviderName.CLAUDE,
                assignment_sequence=2,
                run_id="claude-review-callback",
                status="completed",
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

    def test_callback_cannot_supply_its_own_signature_or_omit_sha_binding(self):
        with self.assertRaisesRegex(ValueError, "must not supply"):
            authenticate_claude_review_artifact(
                self.callback_record(), self.callback(signature="f" * 64), REVIEW_SECRET
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

    def test_conflict_and_draft_fail_closed(self):
        for index, (changes, reason) in enumerate(
            (({"mergeable": False}, "mergeability"), ({"draft": True}, "draft"))
        ):
            with self.subTest(index=index):
                self.assert_rejected(
                    artifact=self.artifact(artifact_id=f"state-{index}"),
                    snapshot=self.snapshot(**changes),
                    reason=reason,
                )

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
