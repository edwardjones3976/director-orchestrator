# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:49 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:14 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:38 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 02:44 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 06:29 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 05:45 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 03:56 UTC | BEGIN]]
"""Tests for deterministic provider-workload policy enforcement."""

from __future__ import annotations

import dataclasses
import json
import unittest

from director_orchestrator.workload_policy import (
    DEFAULT_POLICY,
    COPILOT_LITE_HARD_CEILING,
    COPILOT_LITE_NORMAL_BAND,
    DIRECT_PRODUCTION_TARGETS,
    FABLE_MODEL_FLOOR,
    OVERALL_TARGETS,
    PRIMARY_PROVIDERS,
    AssignmentRequest,
    DecisionOutcome,
    DirectProductionRecord,
    DirectProductionProvider,
    DirectProductionTarget,
    EfficiencySnapshot,
    EnforcementMode,
    FailureKind,
    OverallTarget,
    PrestagePacket,
    Provider,
    RetryRequest,
    Transport,
    TrustedProviderProfile,
    WorkKind,
    WorkSize,
    WorkloadPolicy,
    WorkloadPolicyError,
    WorkloadRecord,
    direct_production_provider,
    evaluate_assignment,
    evaluate_copilot_lite_effort,
    evaluate_efficiency,
    evaluate_retry,
    is_accepted_productive_kind,
    is_direct_production_kind,
    select_cheapest_capable_provider,
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


def balanced_direct_history(
    excluded: DirectProductionProvider, *, total_units: int
) -> tuple[DirectProductionRecord, ...]:
    """Build nineteen valid records whose aggregate effort is ``total_units``."""

    providers = tuple(
        provider for provider in DirectProductionProvider if provider is not excluded
    )
    record_providers = tuple(providers[index % len(providers)] for index in range(19))
    efforts = [1] * len(record_providers)
    remaining = total_units - len(efforts)
    for provider in providers:
        provider_indices = [
            index
            for index, record_provider in enumerate(record_providers)
            if record_provider is provider
        ]
        capacity = (
            DEFAULT_POLICY.direct_production_hard_threshold(provider)
            - len(provider_indices)
        )
        added = min(remaining, capacity)
        efforts[provider_indices[0]] += added
        remaining -= added
        if remaining == 0:
            break
    if remaining:
        raise AssertionError("direct-history fixture exceeds valid provider capacity")
    return tuple(
        DirectProductionRecord(
            f"DIRECT-OTHER-{index:02d}",
            provider,
            effort_units=efforts[index],
        )
        for index, provider in enumerate(record_providers)
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
                Provider.GROK: 11,
                Provider.OPENAI: 8,
                Provider.GEMINI: 20,
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
                DirectProductionProvider.CLAUDE: 25,
                DirectProductionProvider.CURSOR: 20,
                DirectProductionProvider.GROK: 11,
                DirectProductionProvider.GEMINI: 15,
                DirectProductionProvider.PERPLEXITY: 4,
                DirectProductionProvider.EXTERNAL_OPENAI: 25,
            },
        )
        self.assertEqual(sum(actual.values()), 100)
        self.assertNotIn(DirectProductionProvider.EXTERNAL_OPENAI, Provider)

    def test_default_policy_exposes_machine_readable_targets(self):
        self.assertEqual(DEFAULT_POLICY.overall_target(Provider.CLAUDE), 36)
        self.assertEqual(
            DEFAULT_POLICY.direct_production_target(
                DirectProductionProvider.CLAUDE
            ),
            25,
        )
        self.assertEqual(
            DEFAULT_POLICY.direct_production_soft_band(
                DirectProductionProvider.CLAUDE
            ),
            (22, 28),
        )
        self.assertEqual(DEFAULT_POLICY.overall_hard_ceiling(Provider.CURSOR), 33)
        self.assertEqual(DEFAULT_POLICY.overall_normal_band(Provider.PERPLEXITY), (1, 7))
        self.assertEqual(COPILOT_LITE_NORMAL_BAND, (0, 4))
        self.assertEqual(COPILOT_LITE_HARD_CEILING, 6)
        self.assertTrue(evaluate_copilot_lite_effort(current_units=0, total_units=99))
        self.assertFalse(evaluate_copilot_lite_effort(current_units=6, total_units=99))

    def test_target_ledgers_reject_duplicates_or_incomplete_sets(self):
        with self.assertRaisesRegex(WorkloadPolicyError, "duplicate providers"):
            WorkloadPolicy(
                overall_targets=OVERALL_TARGETS[:-1] + (OVERALL_TARGETS[0],)
            )
        with self.assertRaisesRegex(WorkloadPolicyError, "complete provider set"):
            WorkloadPolicy(direct_production_targets=DIRECT_PRODUCTION_TARGETS[:-1])

    def test_target_ledgers_must_total_one_hundred(self):
        changed = tuple(
            dataclasses.replace(
                item,
                percent=item.percent + (1 if item.provider is Provider.CLAUDE else 0),
            )
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
    def test_claude_model_floor_applies_only_when_claude_is_selected(self):
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

                nonclaude = evaluate_assignment(
                    assignment(
                        Provider.CURSOR,
                        kind=WorkKind.IMPLEMENTATION,
                        size=size,
                    )
                )
                self.assertTrue(nonclaude.allowed)
                self.assertFalse(
                    any("model_floor" in item for item in nonclaude.violations)
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
                    "claude_medium_long_implementation_requires_model_floor:"
                    f"{FABLE_MODEL_FLOOR}",
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

    def test_overall_history_reports_prospective_share(self):
        history = records(
            *([Provider.CLAUDE] * 8),
            *([Provider.CURSOR] * 4),
            *([Provider.GROK] * 4),
            *([Provider.GEMINI] * 2),
            Provider.PERPLEXITY,
        )
        result = evaluate_assignment(assignment(Provider.CLAUDE), history=history)

        self.assertFalse(result.allowed)
        self.assertIn("prospective_rolling_share:claude:9/20", result.advisories)
        self.assertIn("total_effort_hard_ceiling_exceeded:claude:41", result.violations)

    def test_skewed_total_effort_history_blocks_new_overage(self):
        history = records(
            *([Provider.CLAUDE] * 9),
            *([Provider.GEMINI] * 10),
        )
        result = evaluate_assignment(assignment(Provider.CLAUDE), history=history)

        self.assertFalse(result.allowed)
        self.assertIn("total_effort_prospective_usage_guard", result.advisories)
        self.assertIn("prospective_rolling_share:claude:10/20", result.advisories)
        self.assertFalse(any("overall" in item for item in result.violations))

    def test_skewed_other_provider_prefers_corrective_cursor(self):
        history = tuple(
            WorkloadRecord(f"CLAUDE-{index:02d}", Provider.CLAUDE)
            for index in range(19)
        )

        result = evaluate_assignment(
            assignment(Provider.CURSOR, assignment_id="CURSOR-20"),
            history=history,
        )

        self.assertTrue(result.allowed)
        self.assertIn("total_effort_prospective_usage_guard", result.advisories)
        self.assertFalse(any("overall" in item for item in result.violations))

    def test_cursor_25_is_allowed_but_grok_inherited_overage_blocks(self):
        for provider in (Provider.CURSOR, Provider.GROK):
            with self.subTest(provider=provider, share="25"):
                other_primary = (
                    Provider.GROK if provider is Provider.CURSOR else Provider.CURSOR
                )
                history = records(
                    *([provider] * 4),
                    *([Provider.CLAUDE] * 8),
                    *([other_primary] * 4),
                    *([Provider.GEMINI] * 2),
                    Provider.PERPLEXITY,
                )
                allowed = evaluate_assignment(assignment(provider), history=history)
                self.assertEqual(allowed.allowed, provider is Provider.CURSOR)
                self.assertIn(
                    f"prospective_rolling_share:{provider.value}:5/20",
                    allowed.advisories,
                )
            with self.subTest(provider=provider, share="30"):
                history = records(
                    *([provider] * 5),
                    *([Provider.GEMINI] * 14),
                )
                reported = evaluate_assignment(assignment(provider), history=history)
                self.assertEqual(reported.allowed, provider is Provider.CURSOR)
                self.assertIn(
                    f"prospective_rolling_share:{provider.value}:6/20",
                    reported.advisories,
                )
                if provider is Provider.GROK:
                    self.assertIn(
                        "total_effort_hard_ceiling_exceeded:grok:16",
                        reported.violations,
                    )

    def test_openai_total_effort_ceiling_is_enforced(self):
        allowed_history = records(
            Provider.OPENAI,
            *([Provider.CLAUDE] * 8),
            *([Provider.CURSOR] * 4),
            *([Provider.GROK] * 4),
            *([Provider.GEMINI] * 2),
        )
        allowed = evaluate_assignment(
            assignment(Provider.OPENAI), history=allowed_history
        )
        blocked_history = records(
            *([Provider.OPENAI] * 2),
            *([Provider.CLAUDE] * 8),
            *([Provider.CURSOR] * 4),
            *([Provider.GROK] * 3),
            *([Provider.GEMINI] * 2),
        )
        blocked = evaluate_assignment(
            assignment(Provider.OPENAI), history=blocked_history
        )

        self.assertTrue(allowed.allowed)
        self.assertFalse(blocked.allowed)
        self.assertIn("prospective_rolling_share:openai:3/20", blocked.advisories)
        self.assertFalse(any("overall" in item for item in blocked.violations))

    def test_total_effort_weight_is_enforced(self):
        history = records(*([Provider.GEMINI] * 19))
        result = evaluate_assignment(
            assignment(Provider.CURSOR, effort_units=20), history=history
        )

        self.assertFalse(result.allowed)
        self.assertIn("prospective_rolling_share:cursor:20/39", result.advisories)

    def test_all_provider_total_effort_hard_ceilings_block_overage(self):
        for provider in Provider:
            with self.subTest(provider=provider):
                history = tuple(
                    WorkloadRecord(f"OVER-{index}", provider)
                    for index in range(19)
                )
                result = evaluate_assignment(
                    assignment(provider, effort_units=20), history=history
                )
                self.assertFalse(result.allowed)
                self.assertIn(
                    f"prospective_rolling_share:{provider.value}:39/39",
                    result.advisories,
                )
                self.assertTrue(
                    any("total_effort_hard_ceiling_exceeded" in item for item in result.violations)
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
        recent_providers = (
            *([Provider.CLAUDE] * 8),
            *([Provider.CURSOR] * 3),
            *([Provider.GROK] * 4),
            *([Provider.GEMINI] * 2),
            *([Provider.PERPLEXITY] * 2),
        )
        recent = tuple(
            WorkloadRecord(f"RECENT-{index}", provider)
            for index, provider in enumerate(recent_providers)
        )
        result = evaluate_assignment(
            assignment(Provider.CURSOR), history=stale + recent
        )

        self.assertTrue(result.allowed)
        self.assertIn("prospective_rolling_share:cursor:4/20", result.advisories)

    def test_short_history_is_advisory_instead_of_a_false_cap_violation(self):
        result = evaluate_assignment(
            assignment(Provider.CURSOR),
            history=records(*([Provider.CURSOR] * 5)),
        )

        self.assertTrue(result.allowed)
        self.assertIn("rolling_window_warming_up:6/20", result.advisories)

    def test_duplicate_overall_assignment_identity_does_not_relabel_history(self):
        history = (WorkloadRecord("ASSIGN-20", Provider.GEMINI),)
        result = evaluate_assignment(assignment(), history=history)

        self.assertTrue(result.allowed)
        self.assertIn(
            "overall_effort_assignment_ids_not_unique", result.advisories
        )
        self.assertEqual(result.violations, ())

    def test_shadow_mode_records_a_would_block_without_blocking(self):
        direct_history = tuple(
            DirectProductionRecord(
                f"CLAUDE-SHADOW-{index}", DirectProductionProvider.CLAUDE
            )
            for index in range(19)
        )
        result = evaluate_assignment(
            assignment(
                Provider.CLAUDE,
                kind=WorkKind.IMPLEMENTATION,
                model_label=FABLE_MODEL_FLOOR,
            ),
            direct_production_history=direct_history,
            mode=EnforcementMode.SHADOW,
        )

        self.assertTrue(result.allowed)
        self.assertTrue(result.would_block)
        self.assertEqual(result.outcome, DecisionOutcome.SHADOW_WOULD_BLOCK)
        self.assertEqual(result.to_dict()["outcome"], "shadow_would_block")


class DirectProductionRoutingTests(unittest.TestCase):
    PROVIDER_CASES = (
        (Provider.CLAUDE, DirectProductionProvider.CLAUDE),
        (Provider.CURSOR, DirectProductionProvider.CURSOR),
        (Provider.GROK, DirectProductionProvider.GROK),
        (Provider.GEMINI, DirectProductionProvider.GEMINI),
        (Provider.PERPLEXITY, DirectProductionProvider.PERPLEXITY),
    )

    @staticmethod
    def _mature_corrective_history() -> tuple[DirectProductionRecord, ...]:
        providers = (
            (DirectProductionProvider.CLAUDE, 7),
            (DirectProductionProvider.CURSOR, 3),
            (DirectProductionProvider.GROK, 1),
            (DirectProductionProvider.GEMINI, 3),
            (DirectProductionProvider.PERPLEXITY, 1),
            (DirectProductionProvider.EXTERNAL_OPENAI, 4),
        )
        return tuple(
            DirectProductionRecord(
                f"MATURE-{provider.value.upper()}-{index:02d}", provider
            )
            for provider, count in providers
            for index in range(count)
        )

    def test_direct_production_thresholds_are_target_plus_five(self):
        for direct_provider in DirectProductionProvider:
            with self.subTest(provider=direct_provider):
                target = DEFAULT_POLICY.direct_production_target(direct_provider)
                self.assertEqual(
                    DEFAULT_POLICY.direct_production_hard_threshold(
                        direct_provider
                    ),
                    target + 5,
                )
                self.assertEqual(
                    DEFAULT_POLICY.direct_production_soft_band(direct_provider),
                    (max(0, target - 3), target + 3),
                )

    def test_direct_production_thresholds_apply_to_twenty_assignment_window(self):
        for runtime_provider, direct_provider in self.PROVIDER_CASES:
            threshold = DEFAULT_POLICY.direct_production_hard_threshold(
                direct_provider
            )
            exact_history = balanced_direct_history(
                direct_provider, total_units=100 - threshold
            )
            with self.subTest(provider=direct_provider, state="exact"):
                exact = evaluate_assignment(
                    assignment(
                        runtime_provider,
                        kind=WorkKind.IMPLEMENTATION,
                        effort_units=threshold,
                    ),
                    direct_production_history=exact_history,
                )
                self.assertTrue(exact.allowed)
                self.assertIn(
                    "prospective_direct_production_share:"
                    f"{direct_provider.value}:{threshold}/100",
                    exact.advisories,
                )

            over_history = balanced_direct_history(
                direct_provider, total_units=100 - (threshold + 1)
            )
            with self.subTest(provider=direct_provider, state="over"):
                over = evaluate_assignment(
                    assignment(
                        runtime_provider,
                        kind=WorkKind.IMPLEMENTATION,
                        effort_units=threshold + 1,
                    ),
                    direct_production_history=over_history,
                )
                self.assertFalse(over.allowed)
                self.assertIn(
                    "direct_production_hard_threshold_exceeded:"
                    f"{direct_provider.value}:{threshold}",
                    over.violations,
                )

    def test_same_provider_direct_history_adds_prospective_pressure(self):
        history = tuple(
            DirectProductionRecord(
                f"DIRECT-CURSOR-{index}",
                DirectProductionProvider.CURSOR,
            )
            for index in range(4)
        ) + tuple(
            DirectProductionRecord(
                f"DIRECT-GEMINI-{index}",
                DirectProductionProvider.GEMINI,
            )
            for index in range(15)
        )

        result = evaluate_assignment(
            assignment(
                Provider.CURSOR,
                kind=WorkKind.IMPLEMENTATION,
                effort_units=2,
            ),
            direct_production_history=history,
        )

        self.assertFalse(result.allowed)
        self.assertIn(
            "prospective_direct_production_share:cursor:6/21",
            result.advisories,
        )
        self.assertIn(
            "direct_production_hard_threshold_exceeded:cursor:25",
            result.violations,
        )

    def test_over_cap_other_provider_is_advisory_for_corrective_candidates(self):
        history = self._mature_corrective_history()

        for runtime_provider in (Provider.CURSOR, Provider.GROK):
            with self.subTest(provider=runtime_provider):
                result = evaluate_assignment(
                    assignment(
                        runtime_provider,
                        assignment_id=f"CORRECTIVE-{runtime_provider.value.upper()}-20",
                        kind=WorkKind.IMPLEMENTATION,
                    ),
                    direct_production_history=history,
                )

                self.assertTrue(result.allowed)
                self.assertEqual(result.violations, ())
                self.assertIn(
                    "direct_production_other_provider_over_hard_threshold:"
                    "claude:30",
                    result.advisories,
                )

    def test_over_cap_candidate_provider_remains_blocked(self):
        result = evaluate_assignment(
            assignment(
                Provider.CLAUDE,
                assignment_id="CLAUDE-OVER-CAP-20",
                kind=WorkKind.IMPLEMENTATION,
                model_label=FABLE_MODEL_FLOOR,
            ),
            direct_production_history=self._mature_corrective_history(),
        )

        self.assertFalse(result.allowed)
        self.assertIn(
            "direct_production_hard_threshold_exceeded:claude:30",
            result.violations,
        )
        self.assertNotIn(
            "direct_production_other_provider_over_hard_threshold:claude:30",
            result.advisories,
        )

    def test_total_effort_guard_does_not_create_direct_production_credit(self):
        overall_history = tuple(
            WorkloadRecord(f"OVERALL-CLAUDE-{index}", Provider.CLAUDE)
            for index in range(19)
        )
        direct_history = tuple(
            DirectProductionRecord(
                f"DIRECT-CURSOR-{index}", DirectProductionProvider.CURSOR
            )
            for index in range(19)
        )

        result = evaluate_assignment(
            assignment(
                Provider.CURSOR,
                assignment_id="CURSOR-PRODUCTIVE-20",
                kind=WorkKind.IMPLEMENTATION,
            ),
            history=overall_history,
            direct_production_history=direct_history,
        )

        self.assertFalse(result.allowed)
        self.assertIn("total_effort_prospective_usage_guard", result.advisories)
        self.assertIn(
            "direct_production_hard_threshold_exceeded:cursor:25",
            result.violations,
        )
        self.assertTrue(
            all(item.startswith("direct_production_") for item in result.violations)
        )

    def test_only_implementation_enters_prospective_direct_production(self):
        history = tuple(
            DirectProductionRecord(
                f"CURSOR-DIRECT-{index:02d}",
                DirectProductionProvider.CURSOR,
            )
            for index in range(19)
        )

        for kind in (WorkKind.REVIEW, WorkKind.RESEARCH, WorkKind.VALIDATION):
            with self.subTest(kind=kind):
                result = evaluate_assignment(
                    assignment(Provider.CURSOR, kind=kind),
                    direct_production_history=history,
                )
                self.assertTrue(result.allowed)
                self.assertNotIn(
                    "prospective_direct_production_share",
                    " ".join(result.advisories),
                )
                self.assertFalse(is_direct_production_kind(kind))
                self.assertFalse(
                    is_accepted_productive_kind(Provider.CURSOR, kind)
                )

        self.assertTrue(is_direct_production_kind(WorkKind.IMPLEMENTATION))

    def test_perplexity_research_is_prospectively_direct_relevant(self):
        threshold = DEFAULT_POLICY.direct_production_hard_threshold(
            DirectProductionProvider.PERPLEXITY
        )
        history = balanced_direct_history(
            DirectProductionProvider.PERPLEXITY,
            total_units=100 - (threshold + 1),
        )

        result = evaluate_assignment(
            assignment(
                Provider.PERPLEXITY,
                assignment_id="PERPLEXITY-RESEARCH-20",
                kind=WorkKind.RESEARCH,
                effort_units=threshold + 1,
            ),
            direct_production_history=history,
        )

        self.assertFalse(is_direct_production_kind(WorkKind.RESEARCH))
        self.assertTrue(
            is_accepted_productive_kind(Provider.PERPLEXITY, WorkKind.RESEARCH)
        )
        self.assertFalse(result.allowed)
        self.assertIn(
            f"prospective_direct_production_share:perplexity:{threshold + 1}/100",
            result.advisories,
        )
        self.assertIn(
            f"direct_production_hard_threshold_exceeded:perplexity:{threshold}",
            result.violations,
        )

    def test_ordinary_openai_does_not_map_to_external_openai_production(self):
        self.assertIsNone(direct_production_provider(Provider.OPENAI))
        for runtime_provider, direct_provider in self.PROVIDER_CASES:
            with self.subTest(provider=runtime_provider):
                self.assertIs(
                    direct_production_provider(runtime_provider), direct_provider
                )

        result = evaluate_assignment(
            assignment(Provider.OPENAI),
            direct_production_history=tuple(
                DirectProductionRecord(
                    f"EXTERNAL-OPENAI-{index}",
                    DirectProductionProvider.EXTERNAL_OPENAI,
                )
                for index in range(19)
            ),
        )
        self.assertNotIn(
            "prospective_direct_production_share", " ".join(result.advisories)
        )
        self.assertNotIn(
            "direct_production_hard_threshold_exceeded",
            " ".join(result.violations),
        )


class TrustedProviderSelectionTests(unittest.TestCase):
    @staticmethod
    def _profiles() -> tuple[TrustedProviderProfile, ...]:
        return (
            TrustedProviderProfile(
                Provider.CURSOR,
                frozenset({WorkKind.IMPLEMENTATION, WorkKind.REVIEW}),
                1,
            ),
            TrustedProviderProfile(
                Provider.CLAUDE,
                frozenset({WorkKind.IMPLEMENTATION, WorkKind.REVIEW}),
                4,
            ),
            TrustedProviderProfile(
                Provider.GROK,
                frozenset({WorkKind.REVIEW}),
                2,
            ),
            TrustedProviderProfile(
                Provider.PERPLEXITY,
                frozenset({WorkKind.RESEARCH}),
                0,
            ),
        )

    @staticmethod
    def _eligible() -> frozenset[Provider]:
        return frozenset(
            {Provider.CURSOR, Provider.CLAUDE, Provider.GROK, Provider.PERPLEXITY}
        )

    def test_cheapest_capable_provider_wins_after_trusted_filters(self):
        selected = select_cheapest_capable_provider(
            assignment_id="SELECT-IMPLEMENTATION-1",
            kind=WorkKind.IMPLEMENTATION,
            effort_units=1,
            trusted_profiles=self._profiles(),
            ready_providers=self._eligible(),
            in_scope_providers=self._eligible(),
            reviewer_independent_providers=self._eligible(),
        )

        self.assertIs(selected, Provider.CURSOR)

    def test_subordinate_producer_scope_uses_separate_independent_reviewer_scope(self):
        profiles = (
            TrustedProviderProfile(
                Provider.GEMINI,
                frozenset({WorkKind.IMPLEMENTATION}),
                1,
            ),
            TrustedProviderProfile(
                Provider.CLAUDE,
                frozenset({WorkKind.IMPLEMENTATION, WorkKind.REVIEW}),
                2,
            ),
        )

        selected = select_cheapest_capable_provider(
            assignment_id="SELECT-SUBORDINATE-GEMINI",
            kind=WorkKind.IMPLEMENTATION,
            effort_units=1,
            trusted_profiles=profiles,
            ready_providers=frozenset({Provider.GEMINI, Provider.CLAUDE}),
            in_scope_providers=frozenset({Provider.GEMINI}),
            reviewer_independent_providers=frozenset({Provider.CLAUDE}),
        )

        self.assertIs(selected, Provider.GEMINI)

    def test_antigravity_is_preferred_while_below_its_normal_minimum(self):
        profiles = (
            TrustedProviderProfile(
                Provider.GEMINI,
                frozenset({WorkKind.IMPLEMENTATION}),
                5,
            ),
            TrustedProviderProfile(
                Provider.CURSOR,
                frozenset({WorkKind.IMPLEMENTATION, WorkKind.REVIEW}),
                1,
            ),
            TrustedProviderProfile(
                Provider.CLAUDE,
                frozenset({WorkKind.IMPLEMENTATION, WorkKind.REVIEW}),
                2,
            ),
        )
        history = records(
            *([Provider.GEMINI] * 2),
            *([Provider.CURSOR] * 5),
            *([Provider.CLAUDE] * 7),
            *([Provider.GROK] * 2),
            Provider.OPENAI,
            *([Provider.PERPLEXITY] * 2),
        )
        selected = select_cheapest_capable_provider(
            assignment_id="SELECT-UNDER-TARGET-ANTIGRAVITY",
            kind=WorkKind.IMPLEMENTATION,
            effort_units=1,
            trusted_profiles=profiles,
            ready_providers=frozenset({Provider.GEMINI, Provider.CURSOR, Provider.CLAUDE}),
            in_scope_providers=frozenset({Provider.GEMINI, Provider.CURSOR, Provider.CLAUDE}),
            reviewer_independent_providers=frozenset({Provider.CURSOR, Provider.CLAUDE}),
            overall_effort_history=history,
        )
        self.assertIs(selected, Provider.GEMINI)
        self.assertEqual(history[0].provider, Provider.GEMINI)

    def test_trusted_explicit_provider_requirement_precedes_cost(self):
        selected = select_cheapest_capable_provider(
            assignment_id="SELECT-IMPLEMENTATION-2",
            kind=WorkKind.IMPLEMENTATION,
            effort_units=1,
            trusted_profiles=self._profiles(),
            ready_providers=self._eligible(),
            in_scope_providers=self._eligible(),
            reviewer_independent_providers=self._eligible(),
            explicit_required_provider=Provider.CLAUDE,
        )

        self.assertIs(selected, Provider.CLAUDE)

    def test_missing_or_model_shaped_routing_facts_fail_closed(self):
        common = {
            "assignment_id": "SELECT-FAIL-CLOSED",
            "kind": WorkKind.IMPLEMENTATION,
            "effort_units": 1,
            "ready_providers": self._eligible(),
            "in_scope_providers": self._eligible(),
            "reviewer_independent_providers": self._eligible(),
        }
        with self.assertRaisesRegex(WorkloadPolicyError, "profiles are required"):
            select_cheapest_capable_provider(trusted_profiles=(), **common)
        with self.assertRaisesRegex(WorkloadPolicyError, "profiles are required"):
            select_cheapest_capable_provider(
                trusted_profiles=(
                    {"provider": "cursor", "trusted_cost_rank": 0},  # type: ignore[arg-type]
                ),
                **common,
            )
        with self.assertRaisesRegex(WorkloadPolicyError, "no trusted capable"):
            select_cheapest_capable_provider(
                trusted_profiles=self._profiles(),
                in_scope_providers=frozenset(),
                **{
                    key: value
                    for key, value in common.items()
                    if key != "in_scope_providers"
                },
            )

    def test_direct_hard_cap_excludes_the_cheapest_provider(self):
        history = tuple(
            DirectProductionRecord(f"CURSOR-{index}", DirectProductionProvider.CURSOR)
            for index in range(5)
        ) + tuple(
            DirectProductionRecord(f"CLAUDE-{index}", DirectProductionProvider.CLAUDE)
            for index in range(5)
        ) + tuple(
            DirectProductionRecord(f"GROK-{index}", DirectProductionProvider.GROK)
            for index in range(3)
        ) + tuple(
            DirectProductionRecord(f"GEMINI-{index}", DirectProductionProvider.GEMINI)
            for index in range(4)
        ) + (
            DirectProductionRecord("PERPLEXITY-0", DirectProductionProvider.PERPLEXITY),
            DirectProductionRecord("EXTERNAL-0", DirectProductionProvider.EXTERNAL_OPENAI),
        )

        selected = select_cheapest_capable_provider(
            assignment_id="SELECT-HARD-CAP",
            kind=WorkKind.IMPLEMENTATION,
            effort_units=1,
            trusted_profiles=self._profiles(),
            ready_providers=self._eligible(),
            in_scope_providers=self._eligible(),
            reviewer_independent_providers=self._eligible(),
            direct_production_history=history,
        )

        self.assertIs(selected, Provider.CLAUDE)
        with self.assertRaisesRegex(WorkloadPolicyError, "not eligible"):
            select_cheapest_capable_provider(
                assignment_id="SELECT-HARD-CAP-EXPLICIT",
                kind=WorkKind.IMPLEMENTATION,
                effort_units=1,
                trusted_profiles=self._profiles(),
                ready_providers=self._eligible(),
                in_scope_providers=self._eligible(),
                reviewer_independent_providers=self._eligible(),
                direct_production_history=history,
                explicit_required_provider=Provider.CURSOR,
            )

    def test_selector_chooses_corrective_provider_when_another_is_over_cap(self):
        history = DirectProductionRoutingTests._mature_corrective_history()

        selected = select_cheapest_capable_provider(
            assignment_id="SELECT-CORRECTIVE-CURSOR",
            kind=WorkKind.IMPLEMENTATION,
            effort_units=1,
            trusted_profiles=self._profiles(),
            ready_providers=self._eligible(),
            in_scope_providers=self._eligible(),
            reviewer_independent_providers=self._eligible(),
            direct_production_history=history,
        )

        self.assertIs(selected, Provider.CURSOR)
        with self.assertRaisesRegex(WorkloadPolicyError, "not eligible"):
            select_cheapest_capable_provider(
                assignment_id="SELECT-OVER-CAP-CLAUDE",
                kind=WorkKind.IMPLEMENTATION,
                effort_units=1,
                trusted_profiles=self._profiles(),
                ready_providers=self._eligible(),
                in_scope_providers=self._eligible(),
                reviewer_independent_providers=self._eligible(),
                direct_production_history=history,
                explicit_required_provider=Provider.CLAUDE,
            )

    def test_reviewer_independence_filters_the_cheapest_reviewer(self):
        selected = select_cheapest_capable_provider(
            assignment_id="SELECT-REVIEW",
            kind=WorkKind.REVIEW,
            effort_units=1,
            trusted_profiles=self._profiles(),
            ready_providers=self._eligible(),
            in_scope_providers=self._eligible(),
            reviewer_independent_providers=frozenset({Provider.CLAUDE}),
        )

        self.assertIs(selected, Provider.CLAUDE)
        with self.assertRaisesRegex(WorkloadPolicyError, "no trusted capable"):
            select_cheapest_capable_provider(
                assignment_id="SELECT-REVIEW-NONE",
                kind=WorkKind.REVIEW,
                effort_units=1,
                trusted_profiles=self._profiles(),
                ready_providers=self._eligible(),
                in_scope_providers=self._eligible(),
                reviewer_independent_providers=frozenset(),
            )


class RetryPolicyTests(unittest.TestCase):
    def test_primary_attempt_is_allowed(self):
        result = evaluate_retry(RetryRequest(attempt_number=1, retry_limit=3))
        self.assertTrue(result.allowed)

    def test_additional_attempt_uses_assignment_budget_and_information_gain(self):
        allowed = evaluate_retry(
            RetryRequest(
                attempt_number=2,
                retry_limit=2,
                previous_failure=FailureKind.TERMINAL,
                corrected_approach=True,
                positive_expected_information_gain=True,
            )
        )
        blocked = evaluate_retry(
            RetryRequest(
                attempt_number=2,
                retry_limit=2,
                previous_failure=FailureKind.TERMINAL,
            )
        )

        self.assertTrue(allowed.allowed)
        self.assertFalse(blocked.allowed)
        self.assertIn(
            "additional_attempt_requires_positive_information_gain_and_changed_basis",
            blocked.violations,
        )

    def test_third_attempt_requires_new_information_or_changed_transport(self):
        no_change = evaluate_retry(
            RetryRequest(
                attempt_number=3,
                retry_limit=3,
                previous_failure=FailureKind.TRANSIENT,
                transport=Transport.PROVIDER_API,
                previous_transport=Transport.PROVIDER_API,
            )
        )
        new_information = evaluate_retry(
            RetryRequest(
                attempt_number=3,
                retry_limit=3,
                previous_failure=FailureKind.TERMINAL,
                transport=Transport.PROVIDER_API,
                previous_transport=Transport.PROVIDER_API,
                materially_new_information=True,
                positive_expected_information_gain=True,
            )
        )
        changed_transport = evaluate_retry(
            RetryRequest(
                attempt_number=3,
                retry_limit=3,
                previous_failure=FailureKind.TRANSIENT,
                transport=Transport.BROWSER,
                previous_transport=Transport.PROVIDER_API,
                positive_expected_information_gain=True,
            )
        )

        self.assertFalse(no_change.allowed)
        self.assertIn(
            "additional_attempt_requires_positive_information_gain_and_changed_basis",
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
                retry_limit=3,
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
                        retry_limit=3,
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
            RetryRequest(
                attempt_number=2,
                retry_limit=2,
                previous_failure=FailureKind.TERMINAL,
            ),
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
        with self.assertRaisesRegex(WorkloadPolicyError, "must not overlap"):
            prestage(
                owned_paths=("private/",),
                prohibited_paths=("private/",),
            )

    def test_packet_normalizes_paths_and_rejects_scope_aliases(self):
        packet = prestage(
            owned_paths=("src//module.py", "tests/./test_module.py"),
            prohibited_paths=("private/",),
        )
        self.assertEqual(packet.owned_paths, ("src/module.py", "tests/test_module.py"))
        self.assertEqual(packet.prohibited_paths, ("private",))

        for owned, prohibited in (
            (("src",), ("src/private",)),
            (("SRC/private",), ("src",)),
        ):
            with self.subTest(owned=owned, prohibited=prohibited):
                with self.assertRaisesRegex(WorkloadPolicyError, "must not overlap"):
                    prestage(owned_paths=owned, prohibited_paths=prohibited)

        for invalid in (
            "../private",
            "/absolute/path",
            r"C:\absolute\path",
            r"C:drive-relative\path",
            "src/**",
            "src/private. ",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(
                    WorkloadPolicyError, "repository-relative"
                ):
                    prestage(owned_paths=(invalid,))

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
            RetryRequest(attempt_number=0, retry_limit=1)


# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 03:56 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 05:45 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 06:29 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 02:44 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:38 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:14 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:49 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | END]]
