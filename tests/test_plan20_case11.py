"""Case 11 through native completion services; no model or external effects."""

from __future__ import annotations

import copy
import hashlib
import json
import unittest
from dataclasses import replace
from pathlib import Path

from director_orchestrator.primary_completion import PrimaryCompletionServices
from tests.test_primary_completion import _completion_evidence, _context


FIXTURES = Path(__file__).parent / "fixtures" / "plan20" / "case-11"
FIXTURE_HASHES = {
    "input.json": "0f145aaec0d4abcf6bbe288bd5f9288464ce22d1ab0cdcc7e473ad03b9452191",
    "expected-evidence.json": "baa75932e8e7496669ab37a4b55f9ee1e925851cf6a398cbc21520f11d832280",
}


class NativeCase11Tests(unittest.TestCase):
    def setUp(self) -> None:
        documents = {}
        for name, expected_hash in FIXTURE_HASHES.items():
            raw = (FIXTURES / name).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), expected_hash)
            documents[name] = json.loads(raw.decode("utf-8-sig"))
        fixture = documents["input.json"]
        expected = documents["expected-evidence.json"]
        self.assertEqual(fixture["case_id"], 11)
        self.assertEqual(expected["case_id"], 11)
        self.assertEqual(fixture["mode"], "INERT_MODEL")
        self.assertEqual(expected["native_acceptance"], "DEFERRED")
        self.assertEqual(expected["baseline_result"], "NOT_RUN")
        self.data = fixture["input"]
        self.expected = expected["expected"]
        self.check = self.data["checks"][0]
        self.verdict = self.data["verdict"]
        self.assertTrue(self.check["passed"])
        self.assertEqual(self.verdict["decision"], "ACCEPT")
        self.assertEqual(self.check["candidate_sha256"], self.verdict["candidate_sha256"])
        self.assertNotEqual(self.data["candidate_sha256"], self.check["candidate_sha256"])

        # Fixture content digests and synthetic Git object IDs are separate bindings.
        self.context_a = _context(
            candidate_sha="a" * 40,
            provider_result_digest="sha256:" + self.check["candidate_sha256"],
        )
        self.context_b = replace(
            self.context_a,
            candidate_sha="b" * 40,
            provider_result_digest="sha256:" + self.data["candidate_sha256"],
        )
        self.evidence = _completion_evidence()
        self.evidence.ledger.document.update(
            expected_head_sha=self.context_a.candidate_sha,
            candidate_result_digest=self.context_a.provider_result_digest,
        )
        for result in self.evidence.results:
            result.document["executed_sha"] = self.context_a.candidate_sha
        self.historical = copy.deepcopy(self.evidence)

    def evaluate(self, context, evidence):
        service = PrimaryCompletionServices(
            packet_result_resolver=lambda _: None,
            completion_evidence_resolver=lambda _: evidence,
            external_effect_resolver=lambda _: None,
        )
        review = service.exact_sha_review(context)
        completion = service.ci_security_completion(context)
        # Project native decisions into the frozen case's evidence vocabulary.
        return {
            "checks": [self.check] if completion.passed else [],
            "verdict": self.verdict if review.accepted else None,
            "releasable": review.accepted and completion.passed,
        }

    def test_candidate_b_discards_a_passed_check_and_accept(self) -> None:
        self.assertEqual(self.evaluate(self.context_a, self.evidence), {
            "checks": [self.check], "verdict": self.verdict, "releasable": True,
        })
        self.assertEqual(self.evaluate(self.context_b, self.evidence), self.expected)
        self.assertEqual(self.evidence, self.historical)

    def test_either_git_head_or_result_digest_change_invalidates_evidence(self) -> None:
        for context in (
            replace(self.context_a, candidate_sha=self.context_b.candidate_sha),
            replace(self.context_a, provider_result_digest=self.context_b.provider_result_digest),
        ):
            with self.subTest(context=context):
                self.assertEqual(self.evaluate(context, self.evidence), self.expected)

    def test_rebinding_b_ledger_does_not_reuse_a_results(self) -> None:
        rebound = copy.deepcopy(self.evidence)
        rebound.ledger.document.update(
            expected_head_sha=self.context_b.candidate_sha,
            candidate_result_digest=self.context_b.provider_result_digest,
        )
        rebound = replace(rebound, ledger=replace(rebound.ledger, digest="sha256:" + "f" * 64))
        self.assertEqual(self.evaluate(self.context_b, rebound), self.expected)
        self.assertEqual(self.evidence, self.historical)


if __name__ == "__main__":
    unittest.main()
