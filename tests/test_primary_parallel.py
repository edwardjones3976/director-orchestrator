# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:49 UTC | BEGIN]]
from __future__ import annotations

import unittest

from director_orchestrator.primary_parallel import (
    ParallelAssignment,
    ParallelDispatchError,
    ParallelDispatchPolicy,
    validate_assignment_dag,
)


DIGEST = "sha256:" + "a" * 64


class ParallelPlanValidationTests(unittest.TestCase):
    def test_dependency_ready_order_is_deterministic_not_input_order(self) -> None:
        ordered = validate_assignment_dag(
            (
                ParallelAssignment("review", 3, "cursor", "cursor-app", ("left", "right")),
                ParallelAssignment("right", 2, "claude", "claude-app"),
                ParallelAssignment("left", 1, "openai", "codex-app"),
            )
        )
        self.assertEqual([item.assignment_id for item in ordered], ["left", "right", "review"])

    def test_cycle_unknown_dependency_duplicate_identity_and_self_edge_fail(self) -> None:
        invalid = (
            (
                ParallelAssignment("a", 1, "openai", "codex", ("b",)),
                ParallelAssignment("b", 2, "claude", "claude", ("a",)),
            ),
            (ParallelAssignment("a", 1, "openai", "codex", ("missing",)),),
            (
                ParallelAssignment("a", 1, "openai", "codex"),
                ParallelAssignment("a", 2, "claude", "claude"),
            ),
            (ParallelAssignment("a", 1, "openai", "codex", ("a",)),),
        )
        for assignments in invalid:
            with self.subTest(assignments=assignments):
                with self.assertRaises(ParallelDispatchError):
                    validate_assignment_dag(assignments)

    def test_write_surfaces_and_dependencies_must_be_unique(self) -> None:
        with self.assertRaisesRegex(ParallelDispatchError, "write surfaces"):
            ParallelAssignment(
                "a", 1, "openai", "codex", write_surfaces=("src/a.py", "src/a.py")
            )
        with self.assertRaisesRegex(ParallelDispatchError, "dependencies"):
            ParallelAssignment("a", 1, "openai", "codex", ("b", "b"))

    def test_concurrency_three_requires_accepted_digest_bound_policy_evidence(self) -> None:
        with self.assertRaisesRegex(ParallelDispatchError, "accepted policy evidence"):
            ParallelDispatchPolicy(max_concurrency=3)
        with self.assertRaisesRegex(ParallelDispatchError, "accepted policy evidence"):
            ParallelDispatchPolicy(
                max_concurrency=3,
                scale_policy_evidence_digest=DIGEST,
                scale_policy_accepted=False,
            )
        policy = ParallelDispatchPolicy(
            max_concurrency=3,
            scale_policy_evidence_digest=DIGEST,
            scale_policy_accepted=True,
            provider_caps={"openai": 1},
            deployment_caps={"codex-app": 1},
        )
        self.assertEqual(policy.max_concurrency, 3)

    def test_retry_limit_preserves_pr80_one_through_three_boundary(self) -> None:
        for value in (0, 4):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ParallelDispatchError, "retry_limit"):
                    ParallelAssignment("a", 1, "openai", "codex", retry_limit=value)


if __name__ == "__main__":
    unittest.main()


# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:49 UTC | END]]
