# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | BEGIN]]
"""Assignment acceptance and project completion are different authorities.

Gate evidence can prove that an assignment's required checks passed at one exact
head. It can never prove that the project is finished: that needs an independent
exact-SHA acceptance, Codex Security, trusted CI, the deterministic merge policy,
and owner or product acceptance, none of which this store holds.
"""

from __future__ import annotations

import copy
import unittest

from director_orchestrator.completion_gates import (
    PROJECT_COMPLETION_AUTHORITIES,
    CompletionGateError,
    aggregate_completion_eligibility,
    aggregate_project_completion_eligibility,
    validate_gate_ledger,
    validate_gate_result,
)

# Imported as a module rather than by name so unittest discovery does not collect
# the existing suite a second time through this module's namespace.
import test_completion_gate_runtime as gate_runtime


def gate_evidence():
    """Build one head-bound ledger with one authenticated PASS result."""

    fixture = gate_runtime.CompletionGateRuntimeTests(
        "test_result_validation_derives_assertions_and_authenticates_full_stream"
    )
    fixture.setUp()
    verified_ledger = fixture.verified_head()
    verified_result = validate_gate_result(
        verified_ledger, fixture.result, context=fixture.context
    )
    return fixture, verified_ledger, verified_result


class AssignmentAcceptanceTests(unittest.TestCase):
    def test_complete_independently_reviewed_evidence_authorizes_acceptance_only(self):
        _, ledger, result = gate_evidence()

        status = aggregate_completion_eligibility(ledger, (result,))

        self.assertEqual(status["scope"], "assignment")
        self.assertTrue(status["gate_evidence_complete"])
        self.assertTrue(status["independent_reviewer_evidence_present"])
        self.assertTrue(status["assignment_acceptance_authorized"])
        # The narrower claim never widens into the broader one.
        self.assertFalse(status["project_completion_authorized"])
        self.assertEqual(
            status["separate_required_authorities"], list(PROJECT_COMPLETION_AUTHORITIES)
        )

    def test_producer_only_evidence_completes_gates_but_not_acceptance(self):
        fixture, ledger, _ = gate_evidence()
        producer_result = copy.deepcopy(fixture.result)
        producer_result["evidence_role"] = "PRODUCER"
        gate_runtime.rebind_result(producer_result)
        verified = validate_gate_result(
            ledger, producer_result, context=fixture.context
        )

        status = aggregate_completion_eligibility(ledger, (verified,))

        self.assertTrue(status["gate_evidence_complete"])
        self.assertFalse(status["independent_reviewer_evidence_present"])
        self.assertFalse(status["assignment_acceptance_authorized"])
        self.assertEqual(status["reason"], "independent_reviewer_evidence_missing")

    def test_a_planned_ledger_authorizes_neither_claim(self):
        fixture, _, _ = gate_evidence()
        planned = validate_gate_ledger(
            fixture.planned_predecessor(),
            context=fixture.context,
            runner_registry=fixture.registry,
        )

        status = aggregate_completion_eligibility(planned, ())

        self.assertFalse(status["assignment_acceptance_authorized"])
        self.assertFalse(status["project_completion_authorized"])


class ProjectCompletionTests(unittest.TestCase):
    @staticmethod
    def accepted(assignment_id: str = "ASSIGN-1") -> dict:
        return {
            "scope": "assignment",
            "assignment_id": assignment_id,
            "gate_evidence_complete": True,
            "assignment_acceptance_authorized": True,
            "project_completion_authorized": False,
        }

    def test_accepted_assignments_alone_never_authorize_project_completion(self):
        status = aggregate_project_completion_eligibility(
            [self.accepted()], required_assignment_ids=["ASSIGN-1"]
        )

        self.assertFalse(status["project_completion_authorized"])
        self.assertEqual(status["reason"], "separate_project_authorities_outstanding")
        self.assertEqual(
            status["outstanding_authorities"], sorted(PROJECT_COMPLETION_AUTHORITIES)
        )

    def test_every_separate_authority_must_be_supplied(self):
        for withheld in PROJECT_COMPLETION_AUTHORITIES:
            with self.subTest(withheld=withheld):
                supplied = frozenset(
                    item for item in PROJECT_COMPLETION_AUTHORITIES if item != withheld
                )
                status = aggregate_project_completion_eligibility(
                    [self.accepted()],
                    required_assignment_ids=["ASSIGN-1"],
                    authenticated_project_authorities=supplied,
                )
                self.assertFalse(status["project_completion_authorized"])
                self.assertEqual(status["outstanding_authorities"], [withheld])

    def test_completion_is_authorized_only_with_every_assignment_and_authority(self):
        status = aggregate_project_completion_eligibility(
            [self.accepted("ASSIGN-1"), self.accepted("ASSIGN-2")],
            required_assignment_ids=["ASSIGN-1", "ASSIGN-2"],
            authenticated_project_authorities=frozenset(PROJECT_COMPLETION_AUTHORITIES),
        )

        self.assertTrue(status["project_completion_authorized"])
        self.assertEqual(status["accepted_assignments"], ["ASSIGN-1", "ASSIGN-2"])

    def test_a_declared_assignment_without_acceptance_blocks_completion(self):
        status = aggregate_project_completion_eligibility(
            [self.accepted("ASSIGN-1")],
            required_assignment_ids=["ASSIGN-1", "ASSIGN-2"],
            authenticated_project_authorities=frozenset(PROJECT_COMPLETION_AUTHORITIES),
        )

        self.assertFalse(status["project_completion_authorized"])
        self.assertEqual(status["outstanding_assignments"], ["ASSIGN-2"])
        self.assertEqual(status["reason"], "assignment_acceptance_incomplete")

    def test_undeclared_assignment_evidence_fails_closed(self):
        status = aggregate_project_completion_eligibility(
            [self.accepted("ASSIGN-1"), self.accepted("ASSIGN-STRAY")],
            required_assignment_ids=["ASSIGN-1"],
            authenticated_project_authorities=frozenset(PROJECT_COMPLETION_AUTHORITIES),
        )

        self.assertFalse(status["project_completion_authorized"])
        self.assertEqual(status["undeclared_assignments"], ["ASSIGN-STRAY"])

    def test_an_empty_or_duplicated_declaration_is_refused(self):
        with self.assertRaises(CompletionGateError):
            aggregate_project_completion_eligibility([], required_assignment_ids=[])
        with self.assertRaises(CompletionGateError):
            aggregate_project_completion_eligibility(
                [], required_assignment_ids=["ASSIGN-1", "ASSIGN-1"]
            )


if __name__ == "__main__":
    unittest.main()
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | END]]
