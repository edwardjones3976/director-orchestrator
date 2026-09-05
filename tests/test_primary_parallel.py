# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:45 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:05 UTC | BEGIN]]
# [[Cursor Agent | Composer 2.5 | 2026-09-05 | 00:00 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:19 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:28 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:31 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:49 UTC | BEGIN]]
from __future__ import annotations

import unittest

from director_orchestrator.primary_parallel import (
    authorize_provider_retry,
    canonical_write_surface,
    ParallelAssignment,
    ParallelDispatchError,
    ParallelDispatchPolicy,
    ProviderTransientDispatchError,
    deterministic_format_roster,
    format_for_attempt,
    provider_transport_kind,
    validate_assignment_dag,
    write_surfaces_overlap,
)
from director_orchestrator.workload_policy import FailureKind, Transport


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

    def test_write_surfaces_are_windows_canonical_and_aliases_fail(self) -> None:
        assignment = ParallelAssignment(
            "a",
            1,
            "openai",
            "codex",
            write_surfaces=("Src\\Feature\\File.PY",),
        )
        self.assertEqual(("src/feature/file.py",), assignment.write_surfaces)
        self.assertEqual("src/a.py", canonical_write_surface("SRC\\A.PY"))
        for surfaces in (
            ("src/a.py", "SRC\\A.PY"),
            ("src", "src/a.py"),
        ):
            with self.subTest(surfaces=surfaces):
                with self.assertRaises(ParallelDispatchError):
                    ParallelAssignment(
                        "a", 1, "openai", "codex", write_surfaces=surfaces
                    )

    def test_write_surfaces_reject_absolute_dot_and_unc_paths(self) -> None:
        for surface in (
            "/src/a.py",
            "C:\\src\\a.py",
            "..\\src\\a.py",
            "src/./a.py",
            "//server/share/a.py",
            " src/a.py",
        ):
            with self.subTest(surface=surface):
                with self.assertRaises(ParallelDispatchError):
                    canonical_write_surface(surface)

    def test_parent_child_and_case_aliases_conflict_before_dispatch(self) -> None:
        self.assertTrue(write_surfaces_overlap(("src",), ("SRC\\a.py",)))
        self.assertTrue(write_surfaces_overlap(("src/a.py",), ("SRC\\A.PY",)))
        self.assertFalse(write_surfaces_overlap(("src/a.py",), ("tests/a.py",)))

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

    def test_all_provider_concurrency_requires_exact_one_slot_roster(self) -> None:
        roster = tuple(f"provider-{index}" for index in range(1, 10))
        caps = {provider: 1 for provider in roster}

        with self.assertRaisesRegex(
            ParallelDispatchError, "exact one-slot provider roster"
        ):
            ParallelDispatchPolicy(max_concurrency=9, provider_caps=caps)
        with self.assertRaisesRegex(
            ParallelDispatchError, "exact one-slot provider roster"
        ):
            ParallelDispatchPolicy(
                max_concurrency=9,
                provider_caps={**caps, roster[0]: 2},
                simultaneous_provider_roster=roster,
            )

        policy = ParallelDispatchPolicy(
            max_concurrency=9,
            provider_caps=caps,
            simultaneous_provider_roster=roster,
        )
        self.assertEqual(9, policy.max_concurrency)
        self.assertEqual(roster, policy.simultaneous_provider_roster)

    def test_retry_limit_is_exactly_three(self) -> None:
        for value in (0, 1, 2, 4):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ParallelDispatchError, "retry_limit"):
                    ParallelAssignment("a", 1, "openai", "codex", retry_limit=value)

    def test_formats_are_exhausted_before_reuse_within_three_attempts(self) -> None:
        roster = deterministic_format_roster(
            (("json", "api"), ("text", "cli"), ("json", "api"))
        )
        self.assertEqual(("json", "api"), format_for_attempt(roster, 1))
        self.assertEqual(("text", "cli"), format_for_attempt(roster, 2))
        self.assertEqual(("json", "api"), format_for_attempt(roster, 3))
        with self.assertRaisesRegex(ParallelDispatchError, "exceeds"):
            deterministic_format_roster(
                (("a", "api"), ("b", "api"), ("c", "api"), ("d", "api"))
            )

    def test_plan_size_and_sequence_have_no_parallel_workload_ceiling(self) -> None:
        assignments = tuple(
            ParallelAssignment(
                f"assignment-{sequence}",
                sequence,
                "openai",
                "codex",
                retry_limit=3,
            )
            for sequence in range(1, 26)
        )

        self.assertEqual(assignments, validate_assignment_dag(assignments))


class ProviderRetryAuthorizationTests(unittest.TestCase):
    def test_transient_failure_with_changed_transport_authorizes_recovery(self) -> None:
        allowed, reason = authorize_provider_retry(
            attempt_number=2,
            previous_failure=FailureKind.TRANSIENT,
            transport_id="cli",
            previous_transport_id="api",
        )
        self.assertTrue(allowed)
        self.assertEqual("provider_retry_authorized", reason)

    def test_terminal_failure_and_ambiguity_never_authorize_retry(self) -> None:
        for failure in (FailureKind.TERMINAL, FailureKind.AMBIGUOUS_PAID_DISPATCH):
            with self.subTest(failure=failure):
                allowed, reason = authorize_provider_retry(
                    attempt_number=2,
                    previous_failure=failure,
                    transport_id="cli",
                    previous_transport_id="api",
                )
                self.assertFalse(allowed)
                if failure is FailureKind.AMBIGUOUS_PAID_DISPATCH:
                    self.assertEqual(
                        "ambiguous_paid_dispatch_must_not_be_repeated", reason
                    )
                else:
                    self.assertEqual(
                        "provider_retry_requires_transient_failure", reason
                    )

    def test_third_attempt_requires_transport_change(self) -> None:
        allowed, reason = authorize_provider_retry(
            attempt_number=3,
            previous_failure=FailureKind.TRANSIENT,
            transport_id="api",
            previous_transport_id="api",
        )
        self.assertFalse(allowed)
        self.assertEqual("provider_retry_evidence_insufficient", reason)

    def test_unknown_transport_ids_never_authorize_recovery(self) -> None:
        for ordinal in (2, 3):
            for previous, current in (
                ("unknown-a", "unknown-b"),
                ("api", "unknown-b"),
                ("unknown-a", "cli"),
            ):
                with self.subTest(ordinal=ordinal, previous=previous, current=current):
                    allowed, reason = authorize_provider_retry(
                        attempt_number=ordinal,
                        previous_failure=FailureKind.TRANSIENT,
                        transport_id=current,
                        previous_transport_id=previous,
                    )
                    self.assertFalse(allowed)
                    self.assertEqual("provider_retry_transport_unknown", reason)

    def test_live_transport_kinds_use_underlying_execution_categories(self) -> None:
        for name, expected in (
            ("responses-api", Transport.PROVIDER_API),
            ("work-iq-rest-production", Transport.PROVIDER_API),
            ("native-cli", Transport.LOCAL_CLI),
            ("public-search-cli", Transport.LOCAL_CLI),
            ("authenticated-windows-host-bridge", Transport.LOCAL_CLI),
        ):
            with self.subTest(name=name):
                self.assertIs(provider_transport_kind(name), expected)
        for ordinal in (2, 3):
            allowed, reason = authorize_provider_retry(
                attempt_number=ordinal, previous_failure=FailureKind.TRANSIENT,
                previous_transport_id="responses-api", transport_id="native-cli")
            self.assertTrue(allowed)
            self.assertEqual(reason, "provider_retry_authorized")

    def test_live_aliases_and_bridge_wrappers_do_not_supply_new_evidence(self) -> None:
        for aliases in (
            ("api", "responses-api", "work-iq-rest-production"),
            ("cli", "native-cli", "public-search-cli", "authenticated-windows-host-bridge"),
        ):
            for previous in aliases:
                for current in aliases:
                    for ordinal in (2, 3):
                        with self.subTest(previous=previous, current=current, ordinal=ordinal):
                            allowed, reason = authorize_provider_retry(
                                attempt_number=ordinal,
                                previous_failure=FailureKind.TRANSIENT,
                                previous_transport_id=previous, transport_id=current)
                            self.assertFalse(allowed)
                            self.assertEqual(reason, "provider_retry_evidence_insufficient")

    def test_third_attempt_accepts_distinct_supported_transport_only(self) -> None:
        allowed, reason = authorize_provider_retry(
            attempt_number=3,
            previous_failure=FailureKind.TRANSIENT,
            transport_id="cli",
            previous_transport_id="api",
        )
        self.assertTrue(allowed)
        self.assertEqual("provider_retry_authorized", reason)

    def test_transient_dispatch_error_is_a_typed_recovery_signal(self) -> None:
        self.assertIsInstance(
            ProviderTransientDispatchError("temporary outage"),
            ProviderTransientDispatchError,
        )


if __name__ == "__main__":
    unittest.main()


# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:49 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:31 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:28 UTC | END]]

# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | END]]
# [[Cursor Agent | Composer 2.5 | 2026-09-05 | 00:00 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:19 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:05 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:45 UTC | END]]
