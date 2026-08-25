# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | BEGIN]]
"""Dependency-ready scheduling semantics, without any parallel external dispatch."""

from __future__ import annotations

import unittest

from director_orchestrator.models import Assignment, ProviderName
from director_orchestrator.scheduling import (
    PARALLEL_EXTERNAL_DISPATCH_PERMITTED,
    SchedulingError,
    dependency_ready_decision,
)


def assignment(sequence: int, *, depends_on: list[int] | None = None) -> Assignment:
    return Assignment(
        sequence=sequence,
        agent=ProviderName.CLAUDE,
        task=f"Bounded assignment {sequence}",
        acceptance=["Report the isolated evidence"],
        depends_on=depends_on or [],
    )


class DependencyReadySchedulingTests(unittest.TestCase):
    def test_independent_assignments_are_all_ready_but_only_one_is_handed_out(self):
        decision = dependency_ready_decision(
            [assignment(1), assignment(2), assignment(3)], completed=[]
        )

        self.assertEqual(decision.ready, (1, 2, 3))
        # Readiness is a set; dispatch is not. Exactly one sequence is offered.
        self.assertEqual(decision.next_sequence, 1)
        self.assertFalse(decision.parallel_external_dispatch_permitted)
        self.assertFalse(PARALLEL_EXTERNAL_DISPATCH_PERMITTED)

    def test_a_dependant_is_blocked_until_its_dependency_is_accepted(self):
        assignments = [assignment(1), assignment(2, depends_on=[1])]

        blocked = dependency_ready_decision(assignments, completed=[])
        self.assertEqual(blocked.ready, (1,))
        self.assertEqual(blocked.missing_dependencies(2), (1,))

        unblocked = dependency_ready_decision(assignments, completed=[1])
        self.assertEqual(unblocked.ready, (2,))
        self.assertEqual(unblocked.missing_dependencies(2), ())

    def test_deferral_propagates_transitively_and_spares_independent_work(self):
        assignments = [
            assignment(1),
            assignment(2, depends_on=[1]),
            assignment(3, depends_on=[2]),
            assignment(4),
        ]

        decision = dependency_ready_decision(
            assignments, completed=[], deferred=[1]
        )

        # 2 depends on deferred 1, and 3 depends on 2, so both stand down rather
        # than reporting a hard block that would stop the task.
        self.assertEqual(decision.deferred, (1, 2, 3))
        self.assertEqual(decision.blocked, {})
        # Assignment 4 is unrelated, so it stays eligible.
        self.assertEqual(decision.ready, (4,))
        self.assertEqual(decision.next_sequence, 4)

    def test_a_deferred_assignment_with_satisfied_dependencies_is_not_ready(self):
        decision = dependency_ready_decision(
            [assignment(1), assignment(2)], completed=[], deferred=[1]
        )

        self.assertEqual(decision.ready, (2,))
        self.assertEqual(decision.deferred, (1,))

    def test_completed_assignments_are_never_offered_again(self):
        decision = dependency_ready_decision(
            [assignment(1), assignment(2, depends_on=[1])], completed=[1, 2]
        )

        self.assertEqual(decision.ready, ())
        self.assertIsNone(decision.next_sequence)
        self.assertEqual(decision.completed, (1, 2))

    def test_an_unusable_graph_fails_closed_rather_than_scheduling_anything(self):
        cases = {
            "duplicate_sequence": [assignment(1), assignment(1)],
            "unknown_dependency": [assignment(2, depends_on=[9])],
            "forward_dependency": [assignment(1, depends_on=[2]), assignment(2)],
        }
        for label, assignments in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(SchedulingError):
                    dependency_ready_decision(assignments, completed=[])

    def test_a_completed_sequence_outside_the_plan_fails_closed(self):
        with self.assertRaises(SchedulingError):
            dependency_ready_decision([assignment(1)], completed=[1, 5])

    def test_duplicate_declared_dependencies_fail_closed(self):
        with self.assertRaises(SchedulingError):
            dependency_ready_decision(
                [assignment(1), assignment(2, depends_on=[1, 1])], completed=[]
            )


if __name__ == "__main__":
    unittest.main()
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | END]]
