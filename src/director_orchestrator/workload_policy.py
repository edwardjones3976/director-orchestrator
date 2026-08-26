# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 05:39 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 03:56 UTC | BEGIN]]
"""Deterministic provider-workload policy and pre-staging contracts.

This module is deliberately self-contained and side-effect free.  It records the
owner-approved workload percentages, validates prospective routing against a
rolling assignment window, and returns explicit shadow or enforced decisions.
It never dispatches a provider, spends budget, changes a repository, or treats a
model-generated suggestion as policy authority.

Two percentage ledgers are intentionally separate:

* ``overall_targets`` covers all provider effort, including direction, review,
  research, validation, and implementation.
* ``direct_production_targets`` covers accepted production output only.  The
  external OpenAI lane is distinct from OpenAI's overall control-plane work.

Keeping the ledgers typed separately prevents the 36 percent overall Claude
target from being confused with the 60 percent direct-production target.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Sequence


_SHA1 = re.compile(r"^[0-9a-f]{40}$")
FABLE_MODEL_FLOOR = "FABLE 5 ULTRA/MAX"


class WorkloadPolicyError(ValueError):
    """Raised when owner policy or evidence is structurally invalid."""


class Provider(str, Enum):
    """Providers represented in the overall-effort ledger."""

    CLAUDE = "claude"
    CURSOR = "cursor"
    GROK = "grok"
    OPENAI = "openai"
    GEMINI = "gemini"
    PERPLEXITY = "perplexity"


class DirectProductionProvider(str, Enum):
    """Providers represented in the direct-production ledger.

    ``EXTERNAL_OPENAI`` is intentionally not ``Provider.OPENAI``: the former is
    the separately assigned one-percent production lane, while the latter also
    performs control-plane work counted in the overall-effort ledger.
    """

    CLAUDE = "claude"
    CURSOR = "cursor"
    GROK = "grok"
    GEMINI = "gemini"
    PERPLEXITY = "perplexity"
    EXTERNAL_OPENAI = "external_openai"


class WorkSize(str, Enum):
    SIMPLE = "simple"
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


class WorkKind(str, Enum):
    IMPLEMENTATION = "implementation"
    REVIEW = "review"
    RESEARCH = "research"
    VALIDATION = "validation"
    COORDINATION = "coordination"


class Transport(str, Enum):
    LOCAL_CLI = "local_cli"
    DIRECTOR_ADAPTER = "director_adapter"
    GITHUB_SLACK = "github_slack"
    PROVIDER_API = "provider_api"
    BROWSER = "browser"


class FailureKind(str, Enum):
    NONE = "none"
    TRANSIENT = "transient"
    TERMINAL = "terminal"
    AMBIGUOUS_PAID_DISPATCH = "ambiguous_paid_dispatch"


class EnforcementMode(str, Enum):
    SHADOW = "shadow"
    ENFORCED = "enforced"


class DecisionOutcome(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"
    SHADOW_WOULD_BLOCK = "shadow_would_block"


@dataclass(frozen=True)
class OverallTarget:
    provider: Provider
    percent: int

    def __post_init__(self) -> None:
        _validate_percent(self.percent, "overall target")


@dataclass(frozen=True)
class DirectProductionTarget:
    provider: DirectProductionProvider
    percent: int

    def __post_init__(self) -> None:
        _validate_percent(self.percent, "direct-production target")


@dataclass(frozen=True)
class PrimaryCap:
    provider: Provider
    percent: int

    def __post_init__(self) -> None:
        _validate_percent(self.percent, "primary cap")


@dataclass(frozen=True)
class HardThreshold:
    """Provider-wide target overage threshold for a rolling window."""

    provider: Provider
    percent: int

    def __post_init__(self) -> None:
        _validate_percent(self.percent, "hard threshold")


def _validate_percent(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorkloadPolicyError(f"{label} must be an integer percentage")
    if value < 0 or value > 100:
        raise WorkloadPolicyError(f"{label} must be between 0 and 100")


OVERALL_TARGETS: tuple[OverallTarget, ...] = (
    OverallTarget(Provider.CLAUDE, 36),
    OverallTarget(Provider.CURSOR, 20),
    OverallTarget(Provider.GROK, 20),
    OverallTarget(Provider.OPENAI, 8),
    OverallTarget(Provider.GEMINI, 11),
    OverallTarget(Provider.PERPLEXITY, 5),
)

DIRECT_PRODUCTION_TARGETS: tuple[DirectProductionTarget, ...] = (
    DirectProductionTarget(DirectProductionProvider.CLAUDE, 60),
    DirectProductionTarget(DirectProductionProvider.CURSOR, 14),
    DirectProductionTarget(DirectProductionProvider.GROK, 14),
    DirectProductionTarget(DirectProductionProvider.GEMINI, 7),
    DirectProductionTarget(DirectProductionProvider.PERPLEXITY, 4),
    DirectProductionTarget(DirectProductionProvider.EXTERNAL_OPENAI, 1),
)

PRIMARY_CAPS: tuple[PrimaryCap, ...] = (
    PrimaryCap(Provider.CLAUDE, 45),
    PrimaryCap(Provider.CURSOR, 27),
    PrimaryCap(Provider.GROK, 27),
    PrimaryCap(Provider.OPENAI, 27),
)

HARD_THRESHOLDS: tuple[HardThreshold, ...] = (
    # Claude's owner-approved special threshold replaces the ordinary 36 + 5.
    HardThreshold(Provider.CLAUDE, 45),
    HardThreshold(Provider.CURSOR, 25),
    HardThreshold(Provider.GROK, 25),
    HardThreshold(Provider.OPENAI, 13),
    HardThreshold(Provider.GEMINI, 16),
    HardThreshold(Provider.PERPLEXITY, 10),
)

PRIMARY_PROVIDERS: frozenset[Provider] = frozenset(
    {Provider.CLAUDE, Provider.CURSOR, Provider.GROK, Provider.OPENAI}
)

TRANSPORT_PRIORITY: tuple[Transport, ...] = (
    Transport.LOCAL_CLI,
    Transport.DIRECTOR_ADAPTER,
    Transport.GITHUB_SLACK,
    Transport.PROVIDER_API,
    Transport.BROWSER,
)


@dataclass(frozen=True)
class WorkloadPolicy:
    """Owner-authored policy values used by deterministic evaluators."""

    overall_targets: tuple[OverallTarget, ...] = OVERALL_TARGETS
    direct_production_targets: tuple[DirectProductionTarget, ...] = (
        DIRECT_PRODUCTION_TARGETS
    )
    hard_thresholds: tuple[HardThreshold, ...] = HARD_THRESHOLDS
    primary_caps: tuple[PrimaryCap, ...] = PRIMARY_CAPS
    rolling_window_size: int = 20
    direct_production_hard_overage_points: int = 5
    claude_model_floor: str = FABLE_MODEL_FLOOR
    approved_claude_model_labels: tuple[str, ...] = (FABLE_MODEL_FLOOR,)
    primary_attempt_limit: int = 1
    transient_recovery_limit: int = 1
    conditional_third_attempt_limit: int = 1
    status_read_target: int = 2
    duplicate_validation_reduction_target_percent: int = 70
    total_action_reduction_target_percent: int = 60
    max_ordinary_actions: int = 5
    enabled_transports: tuple[Transport, ...] = TRANSPORT_PRIORITY
    transport_priority: tuple[Transport, ...] = TRANSPORT_PRIORITY

    def __post_init__(self) -> None:
        # Tuple coercion prevents a caller from smuggling mutable lists into an
        # otherwise frozen policy object.
        for name in (
            "overall_targets",
            "direct_production_targets",
            "hard_thresholds",
            "primary_caps",
            "approved_claude_model_labels",
            "enabled_transports",
            "transport_priority",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))

        _validate_complete_targets(
            self.overall_targets,
            expected=frozenset(Provider),
            label="overall targets",
        )
        if sum(target.percent for target in self.overall_targets) != 100:
            raise WorkloadPolicyError("overall targets must total 100 percent")
        if {target.provider: target.percent for target in self.overall_targets} != {
            Provider.CLAUDE: 36,
            Provider.CURSOR: 20,
            Provider.GROK: 20,
            Provider.OPENAI: 8,
            Provider.GEMINI: 11,
            Provider.PERPLEXITY: 5,
        }:
            raise WorkloadPolicyError("overall targets do not match owner policy")

        _validate_complete_targets(
            self.direct_production_targets,
            expected=frozenset(DirectProductionProvider),
            label="direct-production targets",
        )
        if sum(target.percent for target in self.direct_production_targets) != 100:
            raise WorkloadPolicyError(
                "direct-production targets must total 100 percent"
            )
        if {
            target.provider: target.percent
            for target in self.direct_production_targets
        } != {
            DirectProductionProvider.CLAUDE: 60,
            DirectProductionProvider.CURSOR: 14,
            DirectProductionProvider.GROK: 14,
            DirectProductionProvider.GEMINI: 7,
            DirectProductionProvider.PERPLEXITY: 4,
            DirectProductionProvider.EXTERNAL_OPENAI: 1,
        }:
            raise WorkloadPolicyError(
                "direct-production targets do not match owner policy"
            )

        _validate_complete_targets(
            self.hard_thresholds,
            expected=frozenset(Provider),
            label="hard thresholds",
        )
        if {
            threshold.provider: threshold.percent
            for threshold in self.hard_thresholds
        } != {
            Provider.CLAUDE: 45,
            Provider.CURSOR: 25,
            Provider.GROK: 25,
            Provider.OPENAI: 13,
            Provider.GEMINI: 16,
            Provider.PERPLEXITY: 10,
        }:
            raise WorkloadPolicyError("hard thresholds do not match owner policy")

        _validate_complete_targets(
            self.primary_caps,
            expected=PRIMARY_PROVIDERS,
            label="primary caps",
        )
        cap_map = {cap.provider: cap.percent for cap in self.primary_caps}
        if cap_map[Provider.CLAUDE] != 45:
            raise WorkloadPolicyError("Claude's rolling primary cap must be 45 percent")
        for provider in (Provider.CURSOR, Provider.GROK, Provider.OPENAI):
            if cap_map[provider] != 27:
                raise WorkloadPolicyError(
                    f"{provider.value} rolling primary cap must be 27 percent; only Claude may exceed it"
                )

        _positive_int(self.rolling_window_size, "rolling window size")
        if self.rolling_window_size != 20:
            raise WorkloadPolicyError("rolling window size must be 20 assignments")
        if self.direct_production_hard_overage_points != 5:
            raise WorkloadPolicyError(
                "direct-production hard overage must remain target plus 5 points"
            )
        if not self.claude_model_floor.strip():
            raise WorkloadPolicyError("Claude model floor must not be blank")
        if self.claude_model_floor not in self.approved_claude_model_labels:
            raise WorkloadPolicyError(
                "approved Claude model labels must include the configured floor"
            )
        if len(set(self.approved_claude_model_labels)) != len(
            self.approved_claude_model_labels
        ):
            raise WorkloadPolicyError("approved Claude model labels must be unique")

        for value, label in (
            (self.primary_attempt_limit, "primary attempt limit"),
            (self.transient_recovery_limit, "transient recovery limit"),
            (self.conditional_third_attempt_limit, "conditional third attempt limit"),
            (self.status_read_target, "status read target"),
            (self.max_ordinary_actions, "ordinary action maximum"),
        ):
            _positive_int(value, label)
        if (
            self.primary_attempt_limit,
            self.transient_recovery_limit,
            self.conditional_third_attempt_limit,
        ) != (1, 1, 1):
            raise WorkloadPolicyError(
                "retry policy must be one primary, one transient recovery, and one conditional third attempt"
            )
        if self.status_read_target != 2:
            raise WorkloadPolicyError("status read target must be 2")
        if self.max_ordinary_actions != 5:
            raise WorkloadPolicyError("ordinary action maximum must be 5")
        if self.duplicate_validation_reduction_target_percent != 70:
            raise WorkloadPolicyError(
                "duplicate-validation reduction target must be 70 percent"
            )
        if self.total_action_reduction_target_percent != 60:
            raise WorkloadPolicyError(
                "total-action reduction target must be 60 percent"
            )

        if set(self.enabled_transports) != set(Transport):
            raise WorkloadPolicyError("all transports, including browser, must be enabled")
        if len(set(self.transport_priority)) != len(self.transport_priority):
            raise WorkloadPolicyError("transport priority entries must be unique")
        if set(self.transport_priority) != set(self.enabled_transports):
            raise WorkloadPolicyError(
                "transport priority must contain every enabled transport exactly once"
            )
        if self.transport_priority[-1] is not Transport.BROWSER:
            raise WorkloadPolicyError(
                "browser transport must remain enabled at the lowest priority"
            )

    @classmethod
    def default(cls) -> "WorkloadPolicy":
        return cls()

    def overall_target(self, provider: Provider) -> int:
        return _lookup_percent(self.overall_targets, provider)

    def direct_production_target(
        self, provider: DirectProductionProvider
    ) -> int:
        return _lookup_percent(self.direct_production_targets, provider)

    def direct_production_hard_threshold(
        self, provider: DirectProductionProvider
    ) -> int:
        return min(
            100,
            self.direct_production_target(provider)
            + self.direct_production_hard_overage_points,
        )

    def primary_cap(self, provider: Provider) -> int | None:
        for cap in self.primary_caps:
            if cap.provider is provider:
                return cap.percent
        return None

    def hard_threshold(self, provider: Provider) -> int:
        return _lookup_percent(self.hard_thresholds, provider)

    def transport_rank(self, transport: Transport) -> int:
        """Return a zero-based priority rank; browser is enabled but last."""

        try:
            return self.transport_priority.index(transport)
        except ValueError as exc:  # Defensive for customized/deserialized input.
            raise WorkloadPolicyError("transport is not enabled") from exc


def _validate_complete_targets(
    targets: Sequence[Any], *, expected: frozenset[Any], label: str
) -> None:
    providers = [target.provider for target in targets]
    if len(set(providers)) != len(providers):
        raise WorkloadPolicyError(f"{label} must not contain duplicate providers")
    if frozenset(providers) != expected:
        raise WorkloadPolicyError(f"{label} must contain the complete provider set")


def _lookup_percent(targets: Sequence[Any], provider: Any) -> int:
    for target in targets:
        if target.provider is provider:
            return target.percent
    raise WorkloadPolicyError(f"provider is absent from target ledger: {provider}")


def _positive_int(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise WorkloadPolicyError(f"{label} must be a positive integer")


DEFAULT_POLICY = WorkloadPolicy.default()


@dataclass(frozen=True)
class DecisionResult:
    """A deterministic decision shared by routing, retry, and efficiency gates."""

    mode: EnforcementMode
    outcome: DecisionOutcome
    allowed: bool
    violations: tuple[str, ...] = ()
    advisories: tuple[str, ...] = ()

    @property
    def would_block(self) -> bool:
        return bool(self.violations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "outcome": self.outcome.value,
            "allowed": self.allowed,
            "would_block": self.would_block,
            "violations": list(self.violations),
            "advisories": list(self.advisories),
        }


def _decision(
    mode: EnforcementMode,
    violations: Sequence[str],
    advisories: Sequence[str] = (),
) -> DecisionResult:
    unique_violations = tuple(dict.fromkeys(violations))
    unique_advisories = tuple(dict.fromkeys(advisories))
    if not unique_violations:
        return DecisionResult(
            mode=mode,
            outcome=DecisionOutcome.ALLOW,
            allowed=True,
            violations=(),
            advisories=unique_advisories,
        )
    if mode is EnforcementMode.SHADOW:
        return DecisionResult(
            mode=mode,
            outcome=DecisionOutcome.SHADOW_WOULD_BLOCK,
            allowed=True,
            violations=unique_violations,
            advisories=unique_advisories,
        )
    return DecisionResult(
        mode=mode,
        outcome=DecisionOutcome.BLOCK,
        allowed=False,
        violations=unique_violations,
        advisories=unique_advisories,
    )


@dataclass(frozen=True)
class WorkloadRecord:
    assignment_id: str
    provider: Provider
    effort_units: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.provider, Provider):
            raise WorkloadPolicyError("workload record provider must be a Provider")
        if not self.assignment_id.strip():
            raise WorkloadPolicyError("workload record assignment id must not be blank")
        _positive_int(self.effort_units, "workload record effort units")


@dataclass(frozen=True)
class DirectProductionRecord:
    """One authenticated, Director-accepted production credit."""

    assignment_id: str
    provider: DirectProductionProvider
    effort_units: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.provider, DirectProductionProvider):
            raise WorkloadPolicyError(
                "direct-production record provider must be a DirectProductionProvider"
            )
        if not self.assignment_id.strip():
            raise WorkloadPolicyError(
                "direct-production record assignment id must not be blank"
            )
        _positive_int(
            self.effort_units, "direct-production record effort units"
        )


def direct_production_provider(
    provider: Provider,
) -> DirectProductionProvider | None:
    """Map runtime providers into accepted-production accounting.

    Ordinary OpenAI assignments are control-plane work. The separately
    owner-authorized external OpenAI production lane is not a runtime assignment
    target and therefore cannot be manufactured here.
    """

    if provider is Provider.OPENAI:
        return None
    return DirectProductionProvider(provider.value)


@dataclass(frozen=True)
class AssignmentRequest:
    assignment_id: str
    provider: Provider
    kind: WorkKind
    size: WorkSize
    model_label: str | None = None
    effort_units: int = 1

    def __post_init__(self) -> None:
        if not self.assignment_id.strip():
            raise WorkloadPolicyError("assignment id must not be blank")
        if not isinstance(self.provider, Provider):
            raise WorkloadPolicyError("assignment provider must be a Provider")
        if not isinstance(self.kind, WorkKind):
            raise WorkloadPolicyError("assignment kind must be a WorkKind")
        if not isinstance(self.size, WorkSize):
            raise WorkloadPolicyError("assignment size must be a WorkSize")
        if self.model_label is not None and not self.model_label.strip():
            raise WorkloadPolicyError("model label must be absent or non-blank")
        _positive_int(self.effort_units, "assignment effort units")


def evaluate_assignment(
    request: AssignmentRequest,
    *,
    history: Sequence[WorkloadRecord] = (),
    direct_production_history: Sequence[DirectProductionRecord] = (),
    direct_production_assignment_id: str | None = None,
    available_primary_providers: frozenset[Provider] = PRIMARY_PROVIDERS,
    policy: WorkloadPolicy = DEFAULT_POLICY,
    mode: EnforcementMode = EnforcementMode.ENFORCED,
) -> DecisionResult:
    """Evaluate a prospective assignment without dispatching it.

    The rolling cap uses effort-weighted shares across the candidate plus the
    previous 19 assignments.  It activates only when a complete 20-assignment
    window exists, avoiding mathematically meaningless first-assignment caps.
    """

    if not isinstance(mode, EnforcementMode):
        raise WorkloadPolicyError("mode must be an EnforcementMode")
    records = tuple(history)
    if any(not isinstance(record, WorkloadRecord) for record in records):
        raise WorkloadPolicyError("history must contain only WorkloadRecord values")
    direct_records = tuple(direct_production_history)
    if any(
        not isinstance(record, DirectProductionRecord)
        for record in direct_records
    ):
        raise WorkloadPolicyError(
            "direct-production history must contain only DirectProductionRecord values"
        )
    if direct_production_assignment_id is not None and not (
        isinstance(direct_production_assignment_id, str)
        and direct_production_assignment_id.strip()
    ):
        raise WorkloadPolicyError(
            "direct-production assignment id must be a non-empty string"
        )
    available = frozenset(available_primary_providers)
    if any(not isinstance(provider, Provider) for provider in available):
        raise WorkloadPolicyError(
            "available primary providers must contain only Provider values"
        )
    if not available.issubset(PRIMARY_PROVIDERS):
        raise WorkloadPolicyError(
            "available primary providers cannot contain non-primary providers"
        )

    violations: list[str] = []
    advisories: list[str] = []
    if request.provider in PRIMARY_PROVIDERS:
        if request.provider not in available:
            violations.append(
                f"requested_primary_provider_unavailable:{request.provider.value}"
            )
        other_available = available.difference({request.provider})
        if len(other_available) < 2:
            # Degraded availability never authorizes a silent OpenAI fallback or
            # relaxes a workload ceiling.  It requires an explicit owner choice.
            violations.append("degraded_primary_availability_requires_owner_decision")
        else:
            advisories.append(
                f"primary_ceiling_active_with_other_providers:{len(other_available)}"
            )
    if request.kind is WorkKind.IMPLEMENTATION and request.size in {
        WorkSize.MEDIUM,
        WorkSize.LONG,
    }:
        if request.provider is not Provider.CLAUDE:
            violations.append("medium_long_implementation_requires_claude")
        if request.model_label not in policy.approved_claude_model_labels:
            violations.append(
                f"medium_long_implementation_requires_model_floor:{policy.claude_model_floor}"
            )

    prior = records[-(policy.rolling_window_size - 1) :]
    candidate = WorkloadRecord(
        assignment_id=request.assignment_id,
        provider=request.provider,
        effort_units=request.effort_units,
    )
    window = prior + (candidate,)
    ids = [record.assignment_id for record in window]
    if len(ids) != len(set(ids)):
        violations.append("rolling_window_assignment_ids_not_unique")

    if len(window) < policy.rolling_window_size:
        advisories.append(
            f"rolling_window_warming_up:{len(window)}/{policy.rolling_window_size}"
        )
    else:
        hard_threshold = policy.hard_threshold(request.provider)
        absolute_cap = policy.primary_cap(request.provider)
        total_units = sum(record.effort_units for record in window)
        provider_units = sum(
            record.effort_units
            for record in window
            if record.provider is request.provider
        )
        advisories.append(
            f"prospective_rolling_share:{request.provider.value}:{provider_units}/{total_units}"
        )
        if provider_units * 100 > hard_threshold * total_units:
            violations.append(
                f"overall_hard_threshold_exceeded:{request.provider.value}:{hard_threshold}"
            )
        # The absolute primary ceiling remains active even in degraded mode; the
        # availability violation above prevents fallback from bypassing it.
        if (
            absolute_cap is not None
            and provider_units * 100 > absolute_cap * total_units
        ):
            violations.append(
                f"absolute_primary_ceiling_exceeded:{request.provider.value}:{absolute_cap}"
            )

    direct_provider = direct_production_provider(request.provider)
    if direct_provider is not None and request.kind is not WorkKind.COORDINATION:
        direct_prior = direct_records[-(policy.rolling_window_size - 1) :]
        direct_candidate = DirectProductionRecord(
            assignment_id=(
                direct_production_assignment_id or request.assignment_id
            ),
            provider=direct_provider,
            effort_units=request.effort_units,
        )
        direct_window = direct_prior + (direct_candidate,)
        direct_ids = [record.assignment_id for record in direct_window]
        if len(direct_ids) != len(set(direct_ids)):
            violations.append("direct_production_assignment_ids_not_unique")
        if len(direct_window) < policy.rolling_window_size:
            advisories.append(
                "direct_production_window_warming_up:"
                f"{len(direct_window)}/{policy.rolling_window_size}"
            )
        else:
            direct_total = sum(record.effort_units for record in direct_window)
            direct_provider_units = sum(
                record.effort_units
                for record in direct_window
                if record.provider is direct_provider
            )
            direct_threshold = policy.direct_production_hard_threshold(
                direct_provider
            )
            advisories.append(
                "prospective_direct_production_share:"
                f"{direct_provider.value}:{direct_provider_units}/{direct_total}"
            )
            if direct_provider_units * 100 > direct_threshold * direct_total:
                violations.append(
                    "direct_production_hard_threshold_exceeded:"
                    f"{direct_provider.value}:{direct_threshold}"
                )

    return _decision(mode, violations, advisories)


@dataclass(frozen=True)
class RetryRequest:
    attempt_number: int
    previous_failure: FailureKind = FailureKind.NONE
    transport: Transport = Transport.DIRECTOR_ADAPTER
    previous_transport: Transport | None = None
    materially_new_information: bool = False

    def __post_init__(self) -> None:
        _positive_int(self.attempt_number, "attempt number")
        if not isinstance(self.previous_failure, FailureKind):
            raise WorkloadPolicyError("previous failure must be a FailureKind")
        if not isinstance(self.transport, Transport):
            raise WorkloadPolicyError("transport must be a Transport")
        if self.previous_transport is not None and not isinstance(
            self.previous_transport, Transport
        ):
            raise WorkloadPolicyError("previous transport must be absent or a Transport")
        if not isinstance(self.materially_new_information, bool):
            raise WorkloadPolicyError("materially new information must be boolean")


def evaluate_retry(
    request: RetryRequest,
    *,
    policy: WorkloadPolicy = DEFAULT_POLICY,
    mode: EnforcementMode = EnforcementMode.ENFORCED,
) -> DecisionResult:
    """Enforce one primary, one transient recovery, and a conditional third try."""

    violations: list[str] = []
    advisories: list[str] = []
    if request.transport not in policy.enabled_transports:
        violations.append("transport_disabled")
    elif request.transport is Transport.BROWSER:
        advisories.append("browser_transport_enabled_at_lowest_priority")

    if request.attempt_number == 1:
        if request.previous_failure is not FailureKind.NONE:
            violations.append("primary_attempt_must_not_have_previous_failure")
    elif request.previous_failure is FailureKind.AMBIGUOUS_PAID_DISPATCH:
        violations.append("ambiguous_paid_dispatch_must_not_be_repeated")
    elif request.attempt_number == 2:
        if request.previous_failure is not FailureKind.TRANSIENT:
            violations.append("second_attempt_requires_transient_failure")
    elif request.attempt_number == 3:
        transport_changed = (
            request.previous_transport is not None
            and request.previous_transport is not request.transport
        )
        if not (request.materially_new_information or transport_changed):
            violations.append("third_attempt_requires_new_information_or_transport")
    else:
        violations.append("retry_limit_exceeded")

    return _decision(mode, violations, advisories)


@dataclass(frozen=True)
class EfficiencySnapshot:
    status_reads: int
    duplicate_validations: int
    total_actions: int
    ordinary_workflow: bool = True
    baseline_duplicate_validations: int | None = None
    baseline_total_actions: int | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.status_reads, "status reads"),
            (self.duplicate_validations, "duplicate validations"),
            (self.total_actions, "total actions"),
        ):
            _nonnegative_int(value, label)
        if self.duplicate_validations > self.total_actions:
            raise WorkloadPolicyError(
                "duplicate validations cannot exceed total actions"
            )
        if not isinstance(self.ordinary_workflow, bool):
            raise WorkloadPolicyError("ordinary workflow must be boolean")
        for value, label in (
            (self.baseline_duplicate_validations, "baseline duplicate validations"),
            (self.baseline_total_actions, "baseline total actions"),
        ):
            if value is not None:
                _nonnegative_int(value, label)


def _nonnegative_int(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WorkloadPolicyError(f"{label} must be a non-negative integer")


def _meets_reduction(*, current: int, baseline: int, target_percent: int) -> bool:
    if baseline == 0:
        return current == 0
    return (baseline - current) * 100 >= target_percent * baseline


def evaluate_efficiency(
    snapshot: EfficiencySnapshot,
    *,
    policy: WorkloadPolicy = DEFAULT_POLICY,
    mode: EnforcementMode = EnforcementMode.ENFORCED,
) -> DecisionResult:
    """Evaluate action limits and evidence-backed reduction targets."""

    violations: list[str] = []
    advisories: list[str] = []
    if snapshot.status_reads > policy.status_read_target:
        violations.append("status_read_target_exceeded")
    if snapshot.ordinary_workflow and snapshot.total_actions > policy.max_ordinary_actions:
        violations.append("ordinary_action_maximum_exceeded")

    if snapshot.baseline_duplicate_validations is None:
        advisories.append("duplicate_validation_reduction_not_evaluable")
    elif not _meets_reduction(
        current=snapshot.duplicate_validations,
        baseline=snapshot.baseline_duplicate_validations,
        target_percent=policy.duplicate_validation_reduction_target_percent,
    ):
        violations.append("duplicate_validation_reduction_target_not_met")

    if snapshot.baseline_total_actions is None:
        advisories.append("total_action_reduction_not_evaluable")
    elif not _meets_reduction(
        current=snapshot.total_actions,
        baseline=snapshot.baseline_total_actions,
        target_percent=policy.total_action_reduction_target_percent,
    ):
        violations.append("total_action_reduction_target_not_met")

    return _decision(mode, violations, advisories)


@dataclass(frozen=True)
class PrestagePacket:
    """Immutable, canonical task packet sent once before provider execution."""

    packet_id: str
    goal: str
    terminal_condition: str
    repository: str
    worktree: str
    branch: str
    base_sha: str
    candidate_sha: str | None
    producer: Provider
    independent_reviewer: Provider
    owned_paths: tuple[str, ...]
    prohibited_paths: tuple[str, ...]
    required_tests: tuple[str, ...]
    required_security_evidence: tuple[str, ...]
    budget_units: int
    timeout_seconds: int
    retry_limit: int
    merge_eligibility_conditions: tuple[str, ...]
    stopping_conditions: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "packet_id",
            "goal",
            "terminal_condition",
            "repository",
            "worktree",
            "branch",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise WorkloadPolicyError(f"prestage {name} must not be blank")
        if not _SHA1.fullmatch(self.base_sha):
            raise WorkloadPolicyError("prestage base SHA must be 40 lowercase hex characters")
        if self.candidate_sha is not None and not _SHA1.fullmatch(self.candidate_sha):
            raise WorkloadPolicyError(
                "prestage candidate SHA must be absent or 40 lowercase hex characters"
            )
        if not isinstance(self.producer, Provider) or not isinstance(
            self.independent_reviewer, Provider
        ):
            raise WorkloadPolicyError("prestage producer and reviewer must be Providers")
        if self.producer is self.independent_reviewer:
            raise WorkloadPolicyError("prestage producer and reviewer must be independent")

        collection_names = (
            "owned_paths",
            "prohibited_paths",
            "required_tests",
            "required_security_evidence",
            "merge_eligibility_conditions",
            "stopping_conditions",
        )
        for name in collection_names:
            raw = tuple(getattr(self, name))
            if any(not isinstance(item, str) or not item.strip() for item in raw):
                raise WorkloadPolicyError(
                    f"prestage {name} must contain only non-blank strings"
                )
            if len(set(raw)) != len(raw):
                raise WorkloadPolicyError(f"prestage {name} must not contain duplicates")
            object.__setattr__(self, name, tuple(sorted(raw)))

        for required_name in (
            "owned_paths",
            "required_tests",
            "required_security_evidence",
            "merge_eligibility_conditions",
            "stopping_conditions",
        ):
            if not getattr(self, required_name):
                raise WorkloadPolicyError(f"prestage {required_name} must not be empty")
        if set(self.owned_paths).intersection(self.prohibited_paths):
            raise WorkloadPolicyError(
                "prestage owned and prohibited paths must be disjoint"
            )

        _positive_int(self.budget_units, "prestage budget units")
        _positive_int(self.timeout_seconds, "prestage timeout seconds")
        _positive_int(self.retry_limit, "prestage retry limit")
        if self.retry_limit > 3:
            raise WorkloadPolicyError("prestage retry limit cannot exceed 3 attempts")

    def canonical_payload(self) -> dict[str, Any]:
        """Return a fresh JSON-compatible representation, excluding its digest."""

        return {
            "schema_version": "prestage.v1",
            "packet_id": self.packet_id,
            "goal": self.goal,
            "terminal_condition": self.terminal_condition,
            "repository": self.repository,
            "worktree": self.worktree,
            "branch": self.branch,
            "base_sha": self.base_sha,
            "candidate_sha": self.candidate_sha,
            "producer": self.producer.value,
            "independent_reviewer": self.independent_reviewer.value,
            "owned_paths": list(self.owned_paths),
            "prohibited_paths": list(self.prohibited_paths),
            "required_tests": list(self.required_tests),
            "required_security_evidence": list(
                self.required_security_evidence
            ),
            "budget_units": self.budget_units,
            "timeout_seconds": self.timeout_seconds,
            "retry_limit": self.retry_limit,
            "merge_eligibility_conditions": list(
                self.merge_eligibility_conditions
            ),
            "stopping_conditions": list(self.stopping_conditions),
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.canonical_payload(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @property
    def digest(self) -> str:
        payload = self.canonical_json().encode("utf-8")
        return "sha256:" + hashlib.sha256(payload).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        payload = self.canonical_payload()
        payload["digest"] = self.digest
        return payload


# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 03:56 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 05:39 UTC | END]]
