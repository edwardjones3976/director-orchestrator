# [[Z.ai | GLM-5.3 | 2026-09-27 | 06:39 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 23:37 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 23:28 UTC | BEGIN]]
"""Standing authority consumes authenticated evidence and never retries a merge."""
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from director_orchestrator.hotl_execution import (
    MERGE_CLASSES, STANDING_MERGE_AUTHORITY, TERMS, MergeExecutionBoundary,
    MergeObservation, Outcome, ProposedAction, TermEvidence, decide,
    decide_pre_execution, PRE_EXECUTION_TERMS,
)
from tests.test_merge_eligibility import HEAD, OTHER_HEAD, bundle, policy


class StandingMergeExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 9, 26, 23, 30, tzinfo=timezone.utc)
        # Throwaway keys generated in memory; no key material in files.
        self.private_key = Ed25519PrivateKey.generate()
        self.public_key = self.private_key.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        self.action = ProposedAction("eligible_merge_protected_branch", "example/demo",
                                     "test", HEAD, "reviewer-1")
        evidence = bundle()
        evidence = replace(evidence, independent_reviews=(
            replace(evidence.independent_reviews[0], verdict="ACCEPT"),))
        self.observation = MergeObservation(
            bundle=evidence, pull_number=1, base_sha=OTHER_HEAD, environment="test",
            observed_at=self.now, challenge="", authority_id=STANDING_MERGE_AUTHORITY,
            authority_status="ACTIVE", operating_state="ATTENDED",
            producer_principal="author-1", merging_principal="reviewer-1",
            provenance_status="COMPLETE", protection_status="SATISFIED",
            ruleset_status="SATISFIED", bypass_status="DISABLED",
            cancellation_status="RUNNING", tier_zero_status="CLEAR",
            budget_used=0, budget_limit=1, attempts_used=0, attempt_limit=1,
            hops_used=0, hop_limit=1, base_protected=True,
        )
        self.reads = 0
        self.calls = []
        self.final = None
        self.bad_signature = False
        self.database = str(Path(self.temp.name) / "merge.sqlite3")
        self.boundary = MergeExecutionBoundary(
            repository="example/demo", environment="test", assignment_id="ASSIGN-1", policy=policy(),
            prohibited_paths=("src/private/*",), observer_public_key=self.public_key,
            observe=self.observe, merge=self.merge, audit_path=self.database,
            clock=lambda: self.now,
        )

    def observe(self, action, pull, challenge):
        self.reads += 1
        observation = self.final if self.reads > 1 and self.final else self.observation
        observation = replace(observation, challenge=challenge)
        signature = self.private_key.sign(observation.payload()).hex()
        return observation, "0" * 128 if self.bad_signature else signature

    def merge(self, **kwargs):
        # A real durable reservation and final observation precede the mutation.
        with closing(sqlite3.connect(self.database)) as connection, connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM standing_merge_attempt").fetchone()[0], 1)
        self.assertEqual(self.reads, 2)
        self.calls.append(kwargs)
        return "c" * 40

    def assert_hold(self):
        result = self.boundary.execute(self.action, 1)
        self.assertIs(result.outcome, Outcome.HOLD, result)
        self.assertTrue(result.blockers)
        self.assertEqual(self.calls, [])

    def test_both_branch_classes_merge_in_attended_without_a_deployment_grant(self):
        for index, action_class in enumerate(sorted(MERGE_CLASSES)):
            with self.subTest(action_class=action_class):
                self.boundary._audit_path = str(Path(self.temp.name) / f"{index}.sqlite3")
                self.database = self.boundary._audit_path
                self.reads = 0
                self.action = replace(self.action, action_class=action_class)
                self.observation = replace(self.observation, base_protected=(
                    action_class == "eligible_merge_protected_branch"))
                self.assertIs(self.boundary.execute(self.action, 1).outcome, Outcome.EXECUTE)
                self.assertEqual(self.calls[-1], {
                    "repository": "example/demo", "pull_number": 1,
                    "expected_head_sha": HEAD, "expected_base_sha": OTHER_HEAD,
                    "acting_identity": "reviewer-1", "allow_bypass": False,
                })

    def test_each_execution_safeguard_refuses(self):
        changes = (
            {"authority_id": "unknown"}, {"authority_status": "REVOKED"},
            {"authority_status": "UNKNOWN"}, {"operating_state": "SUSPENDED"},
            {"operating_state": "UNKNOWN"}, {"producer_principal": "reviewer-1"},
            {"producer_principal": ""}, {"merging_principal": "other-reviewer"},
            {"provenance_status": "INCOMPLETE"}, {"protection_status": "UNKNOWN"},
            {"protection_status": "FAILED"}, {"ruleset_status": "FAILED"},
            {"ruleset_status": "UNKNOWN"}, {"bypass_status": "ENABLED"},
            {"cancellation_status": "CANCELLED"}, {"cancellation_status": "UNKNOWN"},
            {"tier_zero_status": "FRAMEWORK_EDIT_NOT_AUTHORIZED"},
            {"budget_used": 1}, {"attempts_used": 1}, {"hops_used": 1},
            {"budget_used": True}, {"budget_limit": 0}, {"hop_limit": -1},
            {"base_protected": None}, {"base_protected": False}, {"base_sha": ""},
            {"pull_number": 2}, {"environment": "other"},
            {"observed_at": self.now - timedelta(seconds=31)},
            {"observed_at": self.now + timedelta(seconds=1)},
            {"observed_at": self.now.replace(tzinfo=None)},
        )
        original = self.observation
        for change in changes:
            with self.subTest(change=change):
                self.observation = replace(original, **change)
                self.assert_hold()

    def test_evidence_model_refusals(self):
        original = self.observation.bundle
        review = original.independent_reviews[0]
        security = original.security_reviews[0]
        ci = original.checks[0]
        changes = (
            {"candidate_head_sha": OTHER_HEAD}, {"repository": "other/repo"},
            {"base_ref": "other"}, {"head_ref": "other/feature"},
            {"material_author_principals": frozenset({"author-1", "reviewer-1"})},
            {"independent_reviews": ()},
            {"independent_reviews": (replace(review, authenticated_principal="other"),)},
            {"independent_reviews": (replace(review, reviewed_head_sha=OTHER_HEAD),)},
            {"independent_reviews": (replace(review, verdict="CHANGES_REQUIRED"),)},
            {"independent_reviews": (replace(review, verdict="APPROVED"),)},
            {"independent_reviews": (review, review)},
            {"security_reviews": ()},
            {"security_reviews": (security, security)},
            {"security_reviews": (replace(security, authenticated_principal=""),)},
            {"security_reviews": (replace(security, reviewed_head_sha=OTHER_HEAD),)},
            {"security_reviews": (replace(security, authenticated_principal="author-1"),)},
            {"security_reviews": (replace(security, authenticated_principal="reviewer-1"),)},
            {"security_reviews": (security, replace(security, verdict="SERIOUS_SAFETY_STOP"))},
            {"checks": ()}, {"checks": (ci, ci)},
            {"checks": (replace(ci, observed_head_sha=OTHER_HEAD),)},
            {"checks": (replace(ci, conclusion="FAILURE"),)},
            {"checks": (replace(ci, trusted_app_verified=False),)},
            {"checks": (replace(ci, trusted_app_verified="false"),)},
            {"authenticated_record_digests": frozenset()},
            {"changed_paths": ()}, {"changed_paths": ("outside/file.py",)},
            {"changed_paths": ("src/private/file.py",)},
            {"changed_paths": ("src/../outside.py",)},
            {"changed_paths": ("src/ok.py", "outside/renamed-from.py")},
            {"assignment_status": {}},
            {"assignment_status": {**original.assignment_status, "assignment_id": "OTHER"}},
        )
        for change in changes:
            with self.subTest(change=change):
                self.observation = replace(self.observation, bundle=replace(original, **change))
                self.assert_hold()

    def test_unsigned_claims_and_tampering_never_execute(self):
        self.bad_signature = True
        self.assert_hold()
        self.assertEqual(self.reads, 1)

    def test_observation_signed_by_a_different_observer_key_never_executes(self):
        # Phase 3: the verifier is the observer's PUBLIC key, so only the
        # matching private half can authenticate; another observer's signature
        # is refused exactly like a forged one.
        self.private_key = Ed25519PrivateKey.generate()
        self.assert_hold()

    def test_a_malformed_signature_string_is_refused_not_raised(self):
        self.boundary._observe = lambda *args: (self.observation, "not-hex")
        self.assert_hold()

    def test_previously_signed_observation_cannot_be_replayed(self):
        stale = self.observe(self.action, 1, "old-challenge")
        self.boundary._observe = lambda *args: stale
        self.assert_hold()

    def test_caller_boolean_terms_cannot_authorize_either_merge(self):
        for action_class in MERGE_CLASSES:
            action = replace(self.action, action_class=action_class)
            for state in ("ATTENDED", "DELEGATED"):
                result = decide(action, {term: TermEvidence(True) for term in TERMS},
                                operating_state=state)
                self.assertIs(result.outcome, Outcome.HOLD)

    def test_unrelated_classes_stay_blocked_in_attended(self):
        for action_class in ("mark_pull_request_ready_for_review",
                             "post_to_own_designated_slack_channel", "execute_small_read_only_task"):
            action = replace(self.action, action_class=action_class)
            self.assertIs(decide(action, {term: TermEvidence(True) for term in TERMS}).outcome,
                          Outcome.HOLD)
            self.assertIs(self.boundary.execute(action, 1).outcome, Outcome.HOLD)
        # The existing pre-execution path still requires its own live grant;
        # standing merge authority never supplies that authorization term.
        evidence = {term: TermEvidence(True) for term in PRE_EXECUTION_TERMS}
        evidence["authorization"] = TermEvidence(False, "ATTENDED: no live grant")
        self.assertIs(decide_pre_execution(action, evidence).outcome, Outcome.HOLD)

    def test_every_other_tier_zero_class_stays_blocked(self):
        for action_class in ("deploy_to_production", "publish", "delete_data", "rewrite_history",
                             "purchase", "billing_change", "rotate_credential", "change_access",
                             "edit_framework", "unknown"):
            action = replace(self.action, action_class=action_class)
            self.assertEqual(decide(action, {term: TermEvidence(True) for term in TERMS}).tier, 0)
            self.assertIs(self.boundary.execute(action, 1).outcome, Outcome.HOLD)

    def test_audit_failure_holds_before_final_read_or_mutation(self):
        self.boundary._audit_path = str(Path(self.temp.name) / "absent" / "audit.db")
        self.assert_hold()
        self.assertEqual(self.reads, 1)

    def test_owner_policy_is_not_relaxed_by_standing_authority(self):
        original = self.boundary.policy
        for change in ({"kill_switch_engaged": True}, {"required_check_names": ()},
                       {"allowed_path_globs": ()}, {"allowed_base_refs": ("other",)}):
            with self.subTest(change=change):
                self.boundary.policy = replace(original, **change)
                self.assert_hold()

    def test_mutation_without_a_confirmed_merge_sha_holds_and_stays_reserved(self):
        calls = []
        def ambiguous(**kwargs):
            calls.append(kwargs)
            return None
        self.boundary._merge = ambiguous
        self.assertIs(self.boundary.execute(self.action, 1).outcome, Outcome.HOLD)
        self.assertIs(self.boundary.execute(self.action, 1).outcome, Outcome.HOLD)
        self.assertEqual(len(calls), 1)

    def test_immediate_revalidation_rejects_each_changed_guard(self):
        changes = ({"authority_status": "REVOKED"}, {"operating_state": "SUSPENDED"},
                   {"cancellation_status": "CANCELLED"}, {"ruleset_status": "FAILED"},
                   {"budget_used": 1}, {"base_sha": "d" * 40},
                   {"bundle": replace(self.observation.bundle, candidate_head_sha=OTHER_HEAD)},
                   {"bundle": replace(self.observation.bundle, checks=())})
        for index, change in enumerate(changes):
            with self.subTest(change=change):
                self.boundary._audit_path = str(Path(self.temp.name) / f"drift{index}.db")
                self.reads = 0
                self.final = replace(self.observation, **change)
                self.assert_hold()
                self.assertEqual(self.reads, 2)

    def test_ambiguous_mutation_is_never_replayed_and_audit_is_immutable(self):
        def ambiguous(**kwargs):
            self.calls.append(kwargs)
            raise TimeoutError("outcome unknown")
        self.boundary._merge = ambiguous
        self.assertIs(self.boundary.execute(self.action, 1).outcome, Outcome.HOLD)
        self.assertEqual(len(self.calls), 1)
        self.assertIs(self.boundary.execute(self.action, 1).outcome, Outcome.HOLD)
        self.assertEqual(len(self.calls), 1)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            for statement in ("DELETE FROM standing_merge_attempt",
                              "UPDATE standing_merge_attempt SET head = 'changed'"):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(statement)


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 23:28 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 23:37 UTC | END]]
# [[Z.ai | GLM-5.3 | 2026-09-27 | 06:39 UTC | END]]
