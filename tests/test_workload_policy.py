# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 03:56 UTC | BEGIN]]
"""Tests for deterministic provider-workload policy enforcement."""

from __future__ import annotations

import dataclasses
import json
import unittest

from director_orchestrator.workload_policy import (
    DEFAULT_POLICY,
    DIRECT_PRODUCTION_TARGETS,
    FABLE_MODEL_FLOOR,
    HARD_THRESHOLDS,
    OVERALL_TARGETS,
    PRIMARY_CAPS,
    PRIMARY_PROVIDERS,
    AssignmentRequest,
    DecisionOutcome,
    DirectProductionProvider,
    DirectProductionTarget,
    EfficiencySnapshot,
    EnforcementMode,
    FailureKind,
    HardThreshold,
    OverallTarget,
    PrestagePacket,
    PrimaryCap,
    Provider,
    RetryRequest,
    Transport,
    WorkKind,
    WorkSize,
    WorkloadPolicy,
    WorkloadPolicyError,
    WorkloadRecord,
    evaluate_assignment,
    evaluate_efficiency,
    evaluate_retry,
)


BASE_SHA = "a" * 40
CANDIDATE_SHA = "b" * 40


def assignment(
    provider: Provider = Provider.CLAUDE,
    *,
    assignment_id: str = "ASSIGN-20",
    kind: WorkKind = WorkKind.REVIEW,
    size: WorkSize = WorkSize.SHORT,
    model_label: str | None = None,
    effort_units: int = 1,
) -> AssignmentRequest:
    return AssignmentRequest(
        assignment_id=assignment_id,
        provider=provider,
        kind=kind,
        size=size,
        model_label=model_label,
        effort_units=effort_units,
    )


def records(*providers: Provider) -> tuple[WorkloadRecord, ...]:
    return tuple(
        WorkloadRecord(f"ASSIGN-{index:02d}", provider)
        for index, provider in enumerate(providers, start=1)
    )


def prestage(**overrides) -> PrestagePacket:
    fields = {
        "packet_id": "PACKET-1",
        "goal": "Implement the approved workload gate",
        "terminal_condition": "Focused tests pass",
        "repository": "owner/director-orchestrator",
        "worktree": r"C:\worktrees\provider-workload-enforcement",
        "branch": "codex/provider-workload-enforcement",
        "base_sha": BASE_SHA,
        "candidate_sha": CANDIDATE_SHA,
        "producer": Provider.CLAUDE,
        "independent_reviewer": Provider.CURSOR,
        "owned_paths": ("tests/test_workload_policy.py", "src/workload_policy.py"),
        "prohibited_paths": ("private/",),
        "required_tests": ("test_workload_policy",),
        "required_security_evidence": ("secret-scan",),
        "budget_units": 100,
        "timeout_seconds": 900,
        "retry_limit": 3,
        "merge_eligibility_conditions": ("exact-sha-review", "ci-green"),
        "stopping_conditions": ("owner-decision-required",),
    }
    fields.update(overrides)
    return PrestagePacket(**fields)


class WorkloadTargetTests(unittest.TestCase):
    def test_overall_targets_are_exact_and_total_one_hundred(self):
        actual = {target.provider: target.percent for target in OVERALL_TARGETS}

        self.assertEqual(
            actual,
            {
                Provider.CLAUDE: 36,
                Provider.CURSOR: 20,
                Provider.GROK: 20,
                Provider.OPENAI: 8,
                Provider.GEMINI: 11,
                Provider.PERPLEXITY: 5,
            },
        )
        self.assertEqual(sum(actual.values()), 100)

    def test_direct_production_targets_are_separate_and_exact(self):
        actual = {
            target.provider: target.percent for target in DIRECT_PRODUCTION_TARGETS
        }

        self.assertEqual(
            actual,
            {
                DirectProductionProvider.CLAUDE: 60,
                DirectProductionProvider.CURSOR: 14,
                DirectProductionProvider.GROK: 14,
                DirectProductionProvider.GEMINI: 7,
                DirectProductionProvider.PERPLEXITY: 4,
                DirectProductionProvider.EXTERNAL_OPENAI: 1,
            },
        )
        self.assertEqual(sum(actual.values()), 100)
        self.assertNotIn(DirectProductionProvider.EXTERNAL_OPENAI, Provider)

    def test_primary_caps_are_exact(self):
        actual = {cap.provider: cap.percent for cap in PRIMARY_CAPS}
        self.assertEqual(
            actual,
            {
                Provider.CLAUDE: 45,
                Provider.CURSOR: 27,
                Provider.GROK: 27,
                Provider.OPENAI: 27,
            },
        )

    def test_hard_thresholds_are_exact_target_overages_with_claude_special_case(self):
        actual = {
            threshold.provider: threshold.percent for threshold in HARD_THRESHOLDS
        }
        self.assertEqual(
            actual,
            {
                Provider.CLAUDE: 45,
                Provider.CURSOR: 25,
                Provider.GROK: 25,
                Provider.OPENAI: 13,
                Provider.GEMINI: 16,
                Provider.PERPLEXITY: 10,
            },
        )
        for provider in (
            Provider.CURSOR,
            Provider.GROK,
            Provider.OPENAI,
            Provider.GEMINI,
            Provider.PERPLEXITY,
        ):
            self.assertEqual(
                actual[provider], DEFAULT_POLICY.overall_target(provider) + 5
            )

    def test_default_policy_exposes_machine_readable_targets(self):
        self.assertEqual(DEFAULT_POLICY.overall_target(Provider.CLAUDE), 36)
        self.assertEqual(
            DEFAULT_POLICY.direct_production_target(
                DirectProductionProvider.CLAUDE
            ),
            60,
        )
        self.assertEqual(DEFAULT_POLICY.primary_cap(Provider.CLAUDE), 45)
        self.assertEqual(DEFAULT_POLICY.hard_threshold(Provider.OPENAI), 13)
        self.assertIsNone(DEFAULT_POLICY.primary_cap(Provider.GEMINI))

    def test_target_ledgers_reject_duplicates_or_incomplete_sets(self):
        with self.assertRaisesRegex(WorkloadPolicyError, "duplicate providers"):
            WorkloadPolicy(
                overall_targets=OVERALL_TARGETS[:-1] + (OVERALL_TARGETS[0],)
            )
        with self.assertRaisesRegex(WorkloadPolicyError, "complete provider set"):
            WorkloadPolicy(direct_production_targets=DIRECT_PRODUCTION_TARGETS[:-1])

    def test_target_ledgers_must_total_one_hundred(self):
        changed = tuple(
            OverallTarget(item.provider, item.percent + (1 if item.provider is Provider.CLAUDE else 0))
            for item in OVERALL_TARGETS
        )
        with self.assertRaisesRegex(WorkloadPolicyError, "must total 100"):
            WorkloadPolicy(overall_targets=changed)

        changed_direct = tuple(
            DirectProductionTarget(
                item.provider,
                item.percent
                + (1 if item.provider is DirectProductionProvider.CLAUDE else 0),
            )
            for item in DIRECT_PRODUCTION_TARGETS
        )
        with self.assertRaisesRegex(WorkloadPolicyError, "must total 100"):
            WorkloadPolicy(direct_production_targets=changed_direct)

    def test_only_claude_may_have_a_cap_above_twenty_seven(self):
        unsafe = tuple(
            PrimaryCap(item.provider, 28 if item.provider is Provider.CURSOR else item.percent)
            for item in PRIMARY_CAPS
        )
        with self.assertRaisesRegex(WorkloadPolicyError, "only Claude"):
            WorkloadPolicy(primary_caps=unsafe)

    def test_nonclaude_primary_caps_cannot_be_silently_lowered_or_relaxed(self):
        changed = tuple(
            PrimaryCap(item.provider, 26 if item.provider is Provider.GROK else item.percent)
            for item in PRIMARY_CAPS
        )
        with self.assertRaisesRegex(WorkloadPolicyError, "must be 27"):
            WorkloadPolicy(primary_caps=changed)

    def test_hard_thresholds_reject_policy_drift(self):
        changed = tuple(
            HardThreshold(
                item.provider,
                item.percent + (1 if item.provider is Provider.OPENAI else 0),
            )
            for item in HARD_THRESHOLDS
        )
        with self.assertRaisesRegex(WorkloadPolicyError, "do not match owner policy"):
            WorkloadPolicy(hard_thresholds=changed)

    def test_browser_remains_enabled_at_lowest_priority(self):
        self.assertIn(Transport.BROWSER, DEFAULT_POLICY.enabled_transports)
        self.assertEqual(DEFAULT_POLICY.transport_priority[-1], Transport.BROWSER)
        self.assertEqual(
            DEFAULT_POLICY.transport_rank(Transport.BROWSER),
            len(DEFAULT_POLICY.transport_priority) - 1,
        )
        with self.assertRaisesRegex(WorkloadPolicyError, "lowest priority"):
            WorkloadPolicy(
                transport_priority=(Transport.BROWSER,)
                + tuple(
                    item
                    for item in DEFAULT_POLICY.transport_priority
                    if item is not Transport.BROWSER
                )
            )


class AssignmentRoutingTests(unittest.TestCase):
    def test_medium_and_long_implementation_require_claude_and_model_floor(self):
        for size in (WorkSize.MEDIUM, WorkSize.LONG):
            with self.subTest(size=size):
                allowed = evaluate_assignment(
                    assignment(
                        Provider.CLAUDE,
                        kind=WorkKind.IMPLEMENTATION,
                        size=size,
                        model_label=FABLE_MODEL_FLOOR,
                    )
                )
                self.assertTrue(allowed.allowed)
                self.assertEqual(allowed.outcome, DecisionOutcome.ALLOW)

                wrong_provider = evaluate_assignment(
                    assignment(
                        Provider.CURSOR,
                        kind=WorkKind.IMPLEMENTATION,
                        size=size,
                        model_label=FABLE_MODEL_FLOOR,
                    )
                )
                self.assertFalse(wrong_provider.allowed)
                self.assertIn(
                    "medium_long_implementation_requires_claude",
                    wrong_provider.violations,
                )

                wrong_model = evaluate_assignment(
                    assignment(
                        Provider.CLAUDE,
                        kind=WorkKind.IMPLEMENTATION,
                        size=size,
                        model_label="FABLE 5 HIGH",
                    )
                )
                self.assertFalse(wrong_model.allowed)
                self.assertIn(
                    f"medium_long_implementation_requires_model_floor:{FABLE_MODEL_FLOOR}",
                    wrong_model.violations,
                )

    def test_short_implementation_does_not_require_claude(self):
        result = evaluate_assignment(
            assignment(
                Provider.OPENAI,
                kind=WorkKind.IMPLEMENTATION,
                size=WorkSize.SHORT,
            )
        )
        self.assertTrue(result.allowed)

    def test_claude_is_allowed_at_exactly_forty_five_percent(self):
        history = records(
            *([Provider.CLAUDE] * 8),
            *([Provider.GEMINI] * 11),
        )
        result = evaluate_assignment(assignment(Provider.CLAUDE), history=history)

        self.assertTrue(result.allowed)
        self.assertIn("prospective_rolling_share:claude:9/20", result.advisories)

    def test_claude_is_blocked_above_forty_five_percent(self):
        history = records(
            *([Provider.CLAUDE] * 9),
            *([Provider.GEMINI] * 10),
        )
        result = evaluate_assignment(assignment(Provider.CLAUDE), history=history)

        self.assertFalse(result.allowed)
        self.assertIn("overall_hard_threshold_exceeded:claude:45", result.violations)
        self.assertIn("absolute_primary_ceiling_exceeded:claude:45", result.violations)

    def test_cursor_and_grok_are_allowed_at_twenty_five_and_blocked_at_thirty(self):
        for provider in (Provider.CURSOR, Provider.GROK):
            with self.subTest(provider=provider, share="25"):
                history = records(
                    *([provider] * 4),
                    *([Provider.GEMINI] * 15),
                )
                allowed = evaluate_assignment(assignment(provider), history=history)
                self.assertTrue(allowed.allowed)
                self.assertIn(
                    f"prospective_rolling_share:{provider.value}:5/20",
                    allowed.advisories,
                )
            with self.subTest(provider=provider, share="30"):
                history = records(
                    *([provider] * 5),
                    *([Provider.GEMINI] * 14),
                )
                blocked = evaluate_assignment(assignment(provider), history=history)
                self.assertFalse(blocked.allowed)
                self.assertIn(
                    f"overall_hard_threshold_exceeded:{provider.value}:{DEFAULT_POLICY.hard_threshold(provider)}",
                    blocked.violations,
                )
                self.assertIn(
                    f"absolute_primary_ceiling_exceeded:{provider.value}:27",
                    blocked.violations,
                )

    def test_openai_hard_threshold_blocks_before_its_absolute_ceiling(self):
        allowed_history = records(
            Provider.OPENAI,
            *([Provider.GEMINI] * 18),
        )
        allowed = evaluate_assignment(
            assignment(Provider.OPENAI), history=allowed_history
        )
        blocked_history = records(
            *([Provider.OPENAI] * 2),
            *([Provider.GEMINI] * 17),
        )
        blocked = evaluate_assignment(
            assignment(Provider.OPENAI), history=blocked_history
        )

        self.assertTrue(allowed.allowed)  # 2/20 = 10%, below the 13% threshold.
        self.assertFalse(blocked.allowed)  # 3/20 = 15%, above the threshold.
        self.assertIn(
            "overall_hard_threshold_exceeded:openai:13", blocked.violations
        )
        self.assertNotIn(
            "absolute_primary_ceiling_exceeded:openai:27", blocked.violations
        )

    def test_cap_uses_effort_weight_within_twenty_assignments(self):
        history = records(*([Provider.GEMINI] * 19))
        result = evaluate_assignment(
            assignment(Provider.CURSOR, effort_units=20), history=history
        )

        self.assertFalse(result.allowed)
        self.assertIn("prospective_rolling_share:cursor:20/39", result.advisories)

    def test_all_provider_hard_thresholds_apply_at_exact_boundary(self):
        for provider in Provider:
            threshold = DEFAULT_POLICY.hard_threshold(provider)
            with self.subTest(provider=provider, state="exact"):
                history = tuple(
                    WorkloadRecord(
                        f"OTHER-{index}",
                        Provider.GEMINI if provider is not Provider.GEMINI else Provider.PERPLEXITY,
                        effort_units=(100 - threshold if index == 0 else 1),
                    )
                    for index in range(19)
                )
                # The eighteen unit-one records above add 18, so offset the
                # first record to keep total effort at exactly 100.
                history = (
                    dataclasses.replace(
                        history[0], effort_units=100 - threshold - 18
                    ),
                ) + history[1:]
                exact = evaluate_assignment(
                    assignment(provider, effort_units=threshold), history=history
                )
                self.assertNotIn(
                    f"overall_hard_threshold_exceeded:{provider.value}:{threshold}",
                    exact.violations,
                )

            with self.subTest(provider=provider, state="over"):
                other = (
                    Provider.GEMINI
                    if provider is not Provider.GEMINI
                    else Provider.PERPLEXITY
                )
                history = tuple(
                    WorkloadRecord(
                        f"OVER-{index}",
                        other,
                        effort_units=(100 - (threshold + 1) - 18 if index == 0 else 1),
                    )
                    for index in range(19)
                )
                over = evaluate_assignment(
                    assignment(provider, effort_units=threshold + 1),
                    history=history,
                )
                self.assertIn(
                    f"overall_hard_threshold_exceeded:{provider.value}:{threshold}",
                    over.violations,
                )

    def test_primary_ceiling_requires_two_other_available_primaries(self):
        result = evaluate_assignment(
            assignment(Provider.CLAUDE),
            available_primary_providers=frozenset(
                {Provider.CLAUDE, Provider.CURSOR}
            ),
        )

        self.assertFalse(result.allowed)
        self.assertIn(
            "degraded_primary_availability_requires_owner_decision",
            result.violations,
        )

    def test_degraded_mode_never_silently_falls_back_to_openai(self):
        result = evaluate_assignment(
            assignment(Provider.OPENAI),
            available_primary_providers=frozenset(
                {Provider.CLAUDE}
            ),
        )

        self.assertFalse(result.allowed)
        self.assertIn("requested_primary_provider_unavailable:openai", result.violations)
        self.assertIn(
            "degraded_primary_availability_requires_owner_decision",
            result.violations,
        )

    def test_primary_availability_rejects_nonprimary_entries(self):
        with self.assertRaisesRegex(WorkloadPolicyError, "non-primary"):
            evaluate_assignment(
                assignment(),
                available_primary_providers=frozenset(
                    set(PRIMARY_PROVIDERS) | {Provider.GEMINI}
                ),
            )

    def test_only_the_latest_nineteen_history_records_are_used(self):
        stale = records(*([Provider.CURSOR] * 30))
        recent = tuple(
            WorkloadRecord(f"RECENT-{index}", Provider.GEMINI)
            for index in range(19)
        )
        result = evaluate_assignment(
            assignment(Provider.CURSOR), history=stale + recent
        )

        self.assertTrue(result.allowed)
        self.assertIn("prospective_rolling_share:cursor:1/20", result.advisories)

    def test_short_history_is_advisory_instead_of_a_false_cap_violation(self):
        result = evaluate_assignment(
            assignment(Provider.CURSOR),
            history=records(*([Provider.CURSOR] * 5)),
        )

        self.assertTrue(result.allowed)
        self.assertIn("rolling_window_warming_up:6/20", result.advisories)

    def test_duplicate_assignment_in_window_fails_closed(self):
        history = (WorkloadRecord("ASSIGN-20", Provider.GEMINI),)
        result = evaluate_assignment(assignment(), history=history)

        self.assertFalse(result.allowed)
        self.assertIn("rolling_window_assignment_ids_not_unique", result.violations)

    def test_shadow_mode_records_a_would_block_without_blocking(self):
        result = evaluate_assignment(
            assignment(
                Provider.GROK,
                kind=WorkKind.IMPLEMENTATION,
                size=WorkSize.LONG,
                model_label="GROK",
            ),
            mode=EnforcementMode.SHADOW,
        )

        self.assertTrue(result.allowed)
        self.assertTrue(result.would_block)
        self.assertEqual(result.outcome, DecisionOutcome.SHADOW_WOULD_BLOCK)
        self.assertEqual(result.to_dict()["outcome"], "shadow_would_block")


class RetryPolicyTests(unittest.TestCase):
    def test_primary_attempt_is_allowed(self):
        result = evaluate_retry(RetryRequest(attempt_number=1))
        self.assertTrue(result.allowed)

    def test_second_attempt_requires_a_transient_failure(self):
        allowed = evaluate_retry(
            RetryRequest(
                attempt_number=2,
                previous_failure=FailureKind.TRANSIENT,
            )
        )
        blocked = evaluate_retry(
            RetryRequest(
                attempt_number=2,
                previous_failure=FailureKind.TERMINAL,
            )
        )

        self.assertTrue(allowed.allowed)
        self.assertFalse(blocked.allowed)
        self.assertIn("second_attempt_requires_transient_failure", blocked.violations)

    def test_third_attempt_requires_new_information_or_changed_transport(self):
        no_change = evaluate_retry(
            RetryRequest(
                attempt_number=3,
                previous_failure=FailureKind.TRANSIENT,
                transport=Transport.PROVIDER_API,
                previous_transport=Transport.PROVIDER_API,
            )
        )
        new_information = evaluate_retry(
            RetryRequest(
                attempt_number=3,
                previous_failure=FailureKind.TERMINAL,
                transport=Transport.PROVIDER_API,
                previous_transport=Transport.PROVIDER_API,
                materially_new_information=True,
            )
        )
        changed_transport = evaluate_retry(
            RetryRequest(
                attempt_number=3,
                previous_failure=FailureKind.TRANSIENT,
                transport=Transport.BROWSER,
                previous_transport=Transport.PROVIDER_API,
            )
        )

        self.assertFalse(no_change.allowed)
        self.assertIn(
            "third_attempt_requires_new_information_or_transport",
            no_change.violations,
        )
        self.assertTrue(new_information.allowed)
        self.assertTrue(changed_transport.allowed)
        self.assertIn(
            "browser_transport_enabled_at_lowest_priority",
            changed_transport.advisories,
        )

    def test_fourth_attempt_is_always_blocked(self):
        result = evaluate_retry(
            RetryRequest(
                attempt_number=4,
                previous_failure=FailureKind.TRANSIENT,
                materially_new_information=True,
                previous_transport=Transport.LOCAL_CLI,
                transport=Transport.BROWSER,
            )
        )
        self.assertFalse(result.allowed)
        self.assertIn("retry_limit_exceeded", result.violations)

    def test_ambiguous_paid_dispatch_is_never_repeated(self):
        for attempt_number in (2, 3):
            with self.subTest(attempt_number=attempt_number):
                result = evaluate_retry(
                    RetryRequest(
                        attempt_number=attempt_number,
                        previous_failure=FailureKind.AMBIGUOUS_PAID_DISPATCH,
                        materially_new_information=True,
                        previous_transport=Transport.LOCAL_CLI,
                        transport=Transport.BROWSER,
                    )
                )
                self.assertFalse(result.allowed)
                self.assertIn(
                    "ambiguous_paid_dispatch_must_not_be_repeated",
                    result.violations,
                )

    def test_retry_violation_can_run_in_shadow_mode(self):
        result = evaluate_retry(
            RetryRequest(attempt_number=2, previous_failure=FailureKind.TERMINAL),
            mode=EnforcementMode.SHADOW,
        )
        self.assertTrue(result.allowed)
        self.assertEqual(result.outcome, DecisionOutcome.SHADOW_WOULD_BLOCK)


class EfficiencyPolicyTests(unittest.TestCase):
    def test_all_exact_targets_are_accepted(self):
        result = evaluate_efficiency(
            EfficiencySnapshot(
                status_reads=2,
                duplicate_validations=3,
                total_actions=4,
                baseline_duplicate_validations=10,
                baseline_total_actions=10,
            )
        )

        self.assertTrue(result.allowed)
        self.assertEqual(result.violations, ())

    def test_status_reads_and_ordinary_actions_have_hard_limits(self):
        result = evaluate_efficiency(
            EfficiencySnapshot(
                status_reads=3,
                duplicate_validations=0,
                total_actions=6,
            )
        )

        self.assertFalse(result.allowed)
        self.assertIn("status_read_target_exceeded", result.violations)
        self.assertIn("ordinary_action_maximum_exceeded", result.violations)

    def test_nonordinary_workflow_does_not_claim_the_five_action_limit(self):
        result = evaluate_efficiency(
            EfficiencySnapshot(
                status_reads=2,
                duplicate_validations=0,
                total_actions=6,
                ordinary_workflow=False,
            )
        )
        self.assertTrue(result.allowed)

    def test_reduction_targets_fail_one_step_below_threshold(self):
        result = evaluate_efficiency(
            EfficiencySnapshot(
                status_reads=2,
                duplicate_validations=4,
                total_actions=5,
                baseline_duplicate_validations=10,
                baseline_total_actions=10,
            )
        )

        self.assertFalse(result.allowed)
        self.assertIn(
            "duplicate_validation_reduction_target_not_met", result.violations
        )
        self.assertIn("total_action_reduction_target_not_met", result.violations)

    def test_missing_baselines_are_reported_without_fabricating_a_reduction(self):
        result = evaluate_efficiency(
            EfficiencySnapshot(
                status_reads=1,
                duplicate_validations=0,
                total_actions=2,
            )
        )

        self.assertTrue(result.allowed)
        self.assertIn(
            "duplicate_validation_reduction_not_evaluable", result.advisories
        )
        self.assertIn("total_action_reduction_not_evaluable", result.advisories)

    def test_zero_baseline_only_passes_with_zero_current_value(self):
        result = evaluate_efficiency(
            EfficiencySnapshot(
                status_reads=1,
                duplicate_validations=1,
                total_actions=1,
                baseline_duplicate_validations=0,
                baseline_total_actions=0,
            )
        )
        self.assertFalse(result.allowed)
        self.assertIn(
            "duplicate_validation_reduction_target_not_met", result.violations
        )
        self.assertIn("total_action_reduction_target_not_met", result.violations)


class PrestagePacketTests(unittest.TestCase):
    def test_packet_is_frozen_canonical_and_json_serializable(self):
        packet = prestage()

        self.assertRegex(packet.digest, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(packet.canonical_json(), packet.canonical_json())
        encoded = json.dumps(packet.to_dict(), sort_keys=True)
        self.assertIn(packet.digest, encoded)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            packet.goal = "mutated"  # type: ignore[misc]

    def test_semantically_identical_collection_order_has_the_same_digest(self):
        first = prestage()
        second = prestage(
            owned_paths=tuple(reversed(first.owned_paths)),
            merge_eligibility_conditions=tuple(
                reversed(first.merge_eligibility_conditions)
            ),
        )

        self.assertEqual(first, second)
        self.assertEqual(first.canonical_json(), second.canonical_json())
        self.assertEqual(first.digest, second.digest)

    def test_packet_digest_changes_when_authoritative_content_changes(self):
        original = prestage()
        changed = prestage(goal="Implement a different approved goal")

        self.assertNotEqual(original.digest, changed.digest)

    def test_candidate_sha_may_be_unbound_during_initial_prestaging(self):
        packet = prestage(candidate_sha=None)
        self.assertIsNone(packet.canonical_payload()["candidate_sha"])

    def test_packet_rejects_malformed_sha_or_same_provider_review(self):
        with self.assertRaisesRegex(WorkloadPolicyError, "base SHA"):
            prestage(base_sha="not-a-sha")
        with self.assertRaisesRegex(WorkloadPolicyError, "must be independent"):
            prestage(independent_reviewer=Provider.CLAUDE)

    def test_packet_rejects_duplicate_or_conflicting_scope(self):
        with self.assertRaisesRegex(WorkloadPolicyError, "must not contain duplicates"):
            prestage(owned_paths=("src/a.py", "src/a.py"))
        with self.assertRaisesRegex(WorkloadPolicyError, "must be disjoint"):
            prestage(
                owned_paths=("private/",),
                prohibited_paths=("private/",),
            )

    def test_packet_requires_tests_security_merge_and_stop_evidence(self):
        for field_name in (
            "required_tests",
            "required_security_evidence",
            "merge_eligibility_conditions",
            "stopping_conditions",
        ):
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(WorkloadPolicyError, "must not be empty"):
                    prestage(**{field_name: ()})

    def test_packet_retry_limit_cannot_exceed_policy_maximum(self):
        with self.assertRaisesRegex(WorkloadPolicyError, "cannot exceed 3"):
            prestage(retry_limit=4)


class InputValidationTests(unittest.TestCase):
    def test_workload_records_and_requests_require_positive_effort(self):
        with self.assertRaisesRegex(WorkloadPolicyError, "positive integer"):
            WorkloadRecord("A", Provider.CLAUDE, 0)
        with self.assertRaisesRegex(WorkloadPolicyError, "positive integer"):
            assignment(effort_units=True)

    def test_efficiency_counts_are_nonnegative_and_consistent(self):
        with self.assertRaisesRegex(WorkloadPolicyError, "non-negative"):
            EfficiencySnapshot(-1, 0, 0)
        with self.assertRaisesRegex(WorkloadPolicyError, "cannot exceed"):
            EfficiencySnapshot(0, 2, 1)

    def test_retry_attempt_number_must_be_positive(self):
        with self.assertRaisesRegex(WorkloadPolicyError, "positive integer"):
            RetryRequest(attempt_number=0)


# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 03:56 UTC | END]]
