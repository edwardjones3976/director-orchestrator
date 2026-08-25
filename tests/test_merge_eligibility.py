# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 18:48 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | BEGIN]]
"""Provider-neutral, deterministic, fail-closed merge eligibility."""

from __future__ import annotations

import hashlib
import unittest

from director_orchestrator.merge_eligibility import (
    CheckEvidence,
    MergeEligibilityError,
    MergeEligibilityPolicy,
    MergeEvidenceBundle,
    ReviewEvidence,
    evaluate_merge_eligibility,
)


HEAD = "a" * 40
OTHER_HEAD = "b" * 40


def digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def accepted_assignment_status(**overrides) -> dict:
    status = {
        "scope": "assignment",
        "assignment_id": "ASSIGN-1",
        "gate_evidence_complete": True,
        "assignment_acceptance_authorized": True,
        "project_completion_authorized": False,
    }
    status.update(overrides)
    return status


def review(principal: str, label: str, *, head: str = HEAD) -> ReviewEvidence:
    return ReviewEvidence(
        authenticated_principal=principal,
        authentication_record_digest=digest(f"auth:{label}"),
        reviewed_head_sha=head,
        verdict="APPROVED",
        record_digest=digest(f"record:{label}"),
    )


def check(name: str, *, head: str = HEAD, conclusion: str = "SUCCESS") -> CheckEvidence:
    return CheckEvidence(
        check_name=name,
        conclusion=conclusion,
        observed_head_sha=head,
        trusted_app_verified=True,
        response_digest=digest(f"check:{name}"),
    )


def policy(**overrides) -> MergeEligibilityPolicy:
    fields = {
        "allowed_base_refs": ("main",),
        "allowed_path_globs": ("src/*", "tests/*"),
        "required_check_names": ("build",),
        "required_branch_prefix": "candidate/",
        "require_owner_authorization": True,
        "kill_switch_engaged": False,
    }
    fields.update(overrides)
    return MergeEligibilityPolicy(**fields)


def bundle(**overrides) -> MergeEvidenceBundle:
    independent = review("reviewer-1", "independent")
    security = review("security-1", "security")
    build = check("build")
    fields = {
        "repository": "example/demo",
        "base_ref": "main",
        "head_ref": "candidate/feature",
        "candidate_head_sha": HEAD,
        "changed_paths": ("src/module.py", "tests/test_module.py"),
        "material_author_principals": frozenset({"author-1"}),
        "assignment_status": accepted_assignment_status(),
        "independent_reviews": (independent,),
        "security_reviews": (security,),
        "checks": (build,),
        "owner_authorization_digest": digest("owner"),
        "authenticated_record_digests": frozenset(
            {
                independent.authentication_record_digest,
                independent.record_digest,
                security.authentication_record_digest,
                security.record_digest,
                build.response_digest,
                digest("owner"),
            }
        ),
    }
    fields.update(overrides)
    return MergeEvidenceBundle(**fields)


class MergeEligibilityTests(unittest.TestCase):
    def test_a_complete_evidence_bundle_is_eligible_and_merges_nothing(self):
        verdict = evaluate_merge_eligibility(bundle(), policy=policy())

        self.assertTrue(verdict["eligible"])
        self.assertEqual(verdict["blocking_reasons"], [])
        self.assertFalse(verdict["merge_performed"])
        self.assertFalse(verdict["authorizes_deployment"])
        self.assertFalse(verdict["authorizes_project_completion"])

    def test_the_verdict_is_deterministic(self):
        first = evaluate_merge_eligibility(bundle(), policy=policy())
        second = evaluate_merge_eligibility(bundle(), policy=policy())
        self.assertEqual(first, second)

    def test_the_kill_switch_blocks_an_otherwise_complete_bundle(self):
        verdict = evaluate_merge_eligibility(
            bundle(), policy=policy(kill_switch_engaged=True)
        )
        self.assertFalse(verdict["eligible"])
        self.assertIn("kill_switch_engaged", verdict["blocking_reasons"])

    def test_review_evidence_for_a_superseded_head_never_carries_forward(self):
        verdict = evaluate_merge_eligibility(
            bundle(independent_reviews=(review("reviewer-1", "old", head=OTHER_HEAD),)),
            policy=policy(),
        )
        self.assertFalse(verdict["eligible"])
        self.assertIn(
            "independent_exact_sha_review_missing", verdict["blocking_reasons"]
        )

    def test_a_material_author_cannot_supply_the_independent_review(self):
        verdict = evaluate_merge_eligibility(
            bundle(
                independent_reviews=(review("author-1", "self"),),
                material_author_principals=frozenset({"author-1"}),
            ),
            policy=policy(),
        )
        self.assertFalse(verdict["eligible"])
        self.assertIn(
            "independent_exact_sha_review_missing", verdict["blocking_reasons"]
        )

    def test_security_reviewer_must_differ_from_the_independent_reviewer(self):
        shared = review("reviewer-1", "shared")
        build = check("build")
        verdict = evaluate_merge_eligibility(
            bundle(
                independent_reviews=(shared,),
                security_reviews=(shared,),
                authenticated_record_digests=frozenset(
                    {
                        shared.authentication_record_digest,
                        shared.record_digest,
                        build.response_digest,
                        digest("owner"),
                    }
                ),
            ),
            policy=policy(),
        )
        self.assertFalse(verdict["eligible"])
        self.assertIn("security_reviewer_not_distinct", verdict["blocking_reasons"])

    def test_unauthenticated_review_records_are_ignored(self):
        verdict = evaluate_merge_eligibility(
            bundle(authenticated_record_digests=frozenset()), policy=policy()
        )
        self.assertFalse(verdict["eligible"])
        for reason in (
            "independent_exact_sha_review_missing",
            "security_review_missing",
            "owner_authorization_missing",
            "required_check_readback_unauthenticated:build",
        ):
            self.assertIn(reason, verdict["blocking_reasons"])

    def test_a_failed_or_untrusted_or_stale_required_check_blocks(self):
        cases = {
            "required_check_not_successful:build": check("build", conclusion="FAILURE"),
            "required_check_head_mismatch:build": check("build", head=OTHER_HEAD),
        }
        for reason, evidence in cases.items():
            with self.subTest(reason=reason):
                verdict = evaluate_merge_eligibility(
                    bundle(checks=(evidence,)), policy=policy()
                )
                self.assertFalse(verdict["eligible"])
                self.assertIn(reason, verdict["blocking_reasons"])

    def test_an_unconfigured_check_or_path_policy_fails_closed(self):
        verdict = evaluate_merge_eligibility(
            bundle(),
            policy=policy(required_check_names=(), allowed_path_globs=()),
        )
        self.assertFalse(verdict["eligible"])
        self.assertIn("required_checks_not_configured", verdict["blocking_reasons"])
        self.assertIn("allowed_paths_not_configured", verdict["blocking_reasons"])

    def test_a_changed_path_outside_the_allowlist_blocks(self):
        verdict = evaluate_merge_eligibility(
            bundle(changed_paths=("src/module.py", "infra/deploy.tf")),
            policy=policy(),
        )
        self.assertFalse(verdict["eligible"])
        self.assertIn("changed_path_outside_allowlist", verdict["blocking_reasons"])

    def test_assignment_acceptance_alone_is_not_enough_and_never_means_completion(self):
        not_accepted = evaluate_merge_eligibility(
            bundle(
                assignment_status=accepted_assignment_status(
                    assignment_acceptance_authorized=False
                )
            ),
            policy=policy(),
        )
        self.assertFalse(not_accepted["eligible"])
        self.assertIn(
            "assignment_acceptance_not_authorized", not_accepted["blocking_reasons"]
        )

        # A bundle that claims project completion from assignment evidence is
        # contradictory and is refused rather than trusted.
        contradictory = evaluate_merge_eligibility(
            bundle(
                assignment_status=accepted_assignment_status(
                    project_completion_authorized=True
                )
            ),
            policy=policy(),
        )
        self.assertFalse(contradictory["eligible"])
        self.assertIn(
            "assignment_status_claims_project_completion",
            contradictory["blocking_reasons"],
        )

    def test_base_ref_and_branch_prefix_are_enforced(self):
        verdict = evaluate_merge_eligibility(
            bundle(base_ref="release", head_ref="other/feature"), policy=policy()
        )
        self.assertFalse(verdict["eligible"])
        self.assertIn("base_ref_not_allowlisted", verdict["blocking_reasons"])
        self.assertIn("head_ref_prefix_not_allowlisted", verdict["blocking_reasons"])

    def test_a_malformed_candidate_head_fails_closed(self):
        verdict = evaluate_merge_eligibility(
            bundle(candidate_head_sha="not-a-sha"), policy=policy()
        )
        self.assertFalse(verdict["eligible"])
        self.assertIn("candidate_head_sha_malformed", verdict["blocking_reasons"])

    def test_an_unusable_policy_is_rejected_at_construction(self):
        with self.assertRaises(MergeEligibilityError):
            MergeEligibilityPolicy(allowed_base_refs=())
        with self.assertRaises(MergeEligibilityError):
            MergeEligibilityPolicy(allowed_path_globs=("src/*", "src/*"))

    def test_no_provider_or_vendor_name_appears_in_the_module(self):
        from pathlib import Path

        import director_orchestrator.merge_eligibility as module
        from director_orchestrator.contribution_tags import parse_contribution_tag

        # Attribution envelopes name the contributing assistant by policy, so they
        # are excluded from the scan. Only lines that parse as a well-formed
        # contribution tag are skipped: prose or code that merely mentions a vendor
        # still fails, and a malformed tag is scanned like any other line.
        lines = Path(module.__file__).read_text(encoding="utf-8").splitlines()
        scanned = [
            line for line in lines if parse_contribution_tag(line) is None
        ]
        tag_count = len(lines) - len(scanned)
        self.assertGreaterEqual(tag_count, 2, "expected an outer tag pair")
        self.assertEqual(tag_count % 2, 0, "expected balanced contribution pairs")

        source = "\n".join(scanned).lower()
        for vendor in ("openai", "claude", "cursor", "gemini", "perplexity", "github"):
            self.assertNotIn(vendor, source)


if __name__ == "__main__":
    unittest.main()
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 18:48 UTC | END]]
