# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:14 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:18 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 02:44 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 04:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 06:29 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 05:39 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 03:56 UTC | BEGIN]]
"""Deterministic provider-workload policy and pre-staging contracts.

This module is deliberately self-contained and side-effect free.  It records the
owner-approved workload percentages, validates prospective routing against a
rolling accepted-production window, and returns explicit shadow or enforced
decisions.
It never dispatches a provider, spends budget, changes a repository, or treats a
model-generated suggestion as policy authority.

Two percentage ledgers are intentionally separate:

* ``overall_targets`` covers authenticated or directly observed provider effort,
  including direction, review, research, validation, and implementation.  It is
  a prospective usage guard: it may prefer an under-target provider or block new
  effort above a hard ceiling, but it never creates production credit.
* ``direct_production_targets`` is the sole workload-allocation blocker and
  covers authenticated, Director-accepted production output only. The external
  OpenAI lane is distinct from OpenAI's overall control-plane work.

Keeping the ledgers typed separately prevents the 36 percent overall Claude
target from being confused with the 25 percent direct-production target.
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
COPILOT_LITE_NORMAL_BAND = (0, 4)
COPILOT_LITE_HARD_CEILING = 6


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
    the separately assigned external production lane, while the latter also
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
    normal_min_percent: int
    normal_max_percent: int
    hard_ceiling_percent: int

    def __post_init__(self) -> None:
        _validate_percent(self.percent, "overall target")
        _validate_percent(self.normal_min_percent, "overall normal minimum")
        _validate_percent(self.normal_max_percent, "overall normal maximum")
        _validate_percent(self.hard_ceiling_percent, "overall hard ceiling")
        if not (
            self.normal_min_percent
            <= self.percent
            <= self.normal_max_percent
            < self.hard_ceiling_percent
        ):
            raise WorkloadPolicyError(
                "overall target must be inside its normal band below its hard ceiling"
            )


@dataclass(frozen=True)
class DirectProductionTarget:
    provider: DirectProductionProvider
    percent: int

    def __post_init__(self) -> None:
        _validate_percent(self.percent, "direct-production target")


def _validate_percent(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorkloadPolicyError(f"{label} must be an integer percentage")
    if value < 0 or value > 100:
        raise WorkloadPolicyError(f"{label} must be between 0 and 100")


OVERALL_TARGETS: tuple[OverallTarget, ...] = (
    OverallTarget(Provider.CLAUDE, 36, 33, 39, 41),
    OverallTarget(Provider.CURSOR, 20, 17, 23, 33),
    OverallTarget(Provider.GROK, 11, 8, 14, 16),
    OverallTarget(Provider.OPENAI, 8, 5, 11, 13),
    OverallTarget(Provider.GEMINI, 20, 17, 23, 25),
    OverallTarget(Provider.PERPLEXITY, 5, 1, 7, 9),
)

DIRECT_PRODUCTION_TARGETS: tuple[DirectProductionTarget, ...] = (
    DirectProductionTarget(DirectProductionProvider.CLAUDE, 25),
    DirectProductionTarget(DirectProductionProvider.CURSOR, 20),
    DirectProductionTarget(DirectProductionProvider.GROK, 11),
    DirectProductionTarget(DirectProductionProvider.GEMINI, 15),
    DirectProductionTarget(DirectProductionProvider.PERPLEXITY, 4),
    DirectProductionTarget(DirectProductionProvider.EXTERNAL_OPENAI, 25),
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
    rolling_window_size: int = 20
    direct_production_soft_tolerance_points: int = 3
    direct_production_hard_overage_points: int = 5
    claude_model_floor: str = FABLE_MODEL_FLOOR
    approved_claude_model_labels: tuple[str, ...] = (FABLE_MODEL_FLOOR,)
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
            Provider.GROK: 11,
            Provider.OPENAI: 8,
            Provider.GEMINI: 20,
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
            DirectProductionProvider.CLAUDE: 25,
            DirectProductionProvider.CURSOR: 20,
            DirectProductionProvider.GROK: 11,
            DirectProductionProvider.GEMINI: 15,
            DirectProductionProvider.PERPLEXITY: 4,
            DirectProductionProvider.EXTERNAL_OPENAI: 25,
        }:
            raise WorkloadPolicyError(
                "direct-production targets do not match owner policy"
            )

        _positive_int(self.rolling_window_size, "rolling window size")
        if self.rolling_window_size != 20:
            raise WorkloadPolicyError("rolling window size must be 20 assignments")
        if self.direct_production_soft_tolerance_points != 3:
            raise WorkloadPolicyError(
                "direct-production soft tolerance must remain plus or minus 3 points"
            )
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
            (self.status_read_target, "status read target"),
            (self.max_ordinary_actions, "ordinary action maximum"),
        ):
            _positive_int(value, label)
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

    def overall_normal_band(self, provider: Provider) -> tuple[int, int]:
        target = _lookup_target(self.overall_targets, provider)
        return target.normal_min_percent, target.normal_max_percent

    def overall_hard_ceiling(self, provider: Provider) -> int:
        return _lookup_target(self.overall_targets, provider).hard_ceiling_percent

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

    def direct_production_soft_band(
        self, provider: DirectProductionProvider
    ) -> tuple[int, int]:
        """Return the report/routing band around a direct-production target."""

        target = self.direct_production_target(provider)
        return (
            max(0, target - self.direct_production_soft_tolerance_points),
            min(100, target + self.direct_production_soft_tolerance_points),
        )

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
    return _lookup_target(targets, provider).percent


def _lookup_target(targets: Sequence[Any], provider: Any) -> Any:
    for target in targets:
        if target.provider is provider:
            return target
    raise WorkloadPolicyError(f"provider is absent from target ledger: {provider}")


def _positive_int(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise WorkloadPolicyError(f"{label} must be a positive integer")


def evaluate_copilot_lite_effort(
    *, current_units: int, total_units: int, candidate_units: int = 1
) -> bool:
    """Fail closed when prospective unscored Copilot hygiene exceeds six percent.

    Copilot/Lite is not a production provider and never earns a target or direct
    credit.  This helper exists for the optional one-time hygiene lane; there is
    deliberately no provider adapter or automatic dispatch authority.
    """

    _nonnegative_int(current_units, "Copilot current effort units")
    _nonnegative_int(total_units, "total observed effort units")
    _positive_int(candidate_units, "Copilot candidate effort units")
    prospective_total = total_units + candidate_units
    prospective_copilot = current_units + candidate_units
    return (
        prospective_copilot * 100
        <= COPILOT_LITE_HARD_CEILING * prospective_total
    )


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


def is_direct_production_kind(kind: WorkKind) -> bool:
    """Return whether a canonical work kind can create production credit."""

    if not isinstance(kind, WorkKind):
        raise WorkloadPolicyError("direct-production kind must be a WorkKind")
    return kind is WorkKind.IMPLEMENTATION


def is_accepted_productive_kind(provider: Provider, kind: WorkKind) -> bool:
    """Return whether accepted work may enter the productive-work ledger.

    Implementation remains productive work for repository-capable providers.
    Perplexity's narrow, authenticated public-research product is the sole
    non-code exception; this does not grant Perplexity programming, review,
    voting, merge, Slack, or operational authority.
    """

    if not isinstance(provider, Provider) or not isinstance(kind, WorkKind):
        raise WorkloadPolicyError(
            "accepted productive-work classification requires typed provider and kind"
        )
    return is_direct_production_kind(kind) or (
        provider is Provider.PERPLEXITY and kind is WorkKind.RESEARCH
    )


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


@dataclass(frozen=True)
class TrustedProviderProfile:
    """Owner/system-authored routing facts; never populate this from model output.

    ``trusted_cost_rank`` is an ordinal supplied by authenticated configuration;
    lower values are cheaper. It is deliberately not a model-proposed dollar
    estimate, and equal ranks are resolved by the stable provider identifier.
    """

    provider: Provider
    supported_work_kinds: frozenset[WorkKind]
    trusted_cost_rank: int

    def __post_init__(self) -> None:
        if not isinstance(self.provider, Provider):
            raise WorkloadPolicyError(
                "trusted provider profile provider must be a Provider"
            )
        try:
            supported = frozenset(self.supported_work_kinds)
        except TypeError as exc:
            raise WorkloadPolicyError(
                "trusted provider capabilities must be an iterable of WorkKind values"
            ) from exc
        if not supported or any(
            not isinstance(kind, WorkKind) for kind in supported
        ):
            raise WorkloadPolicyError(
                "trusted provider capabilities must be a non-empty set of WorkKind values"
            )
        if (
            isinstance(self.trusted_cost_rank, bool)
            or not isinstance(self.trusted_cost_rank, int)
            or self.trusted_cost_rank < 0
        ):
            raise WorkloadPolicyError(
                "trusted provider cost rank must be a non-negative integer"
            )
        object.__setattr__(self, "supported_work_kinds", supported)


def _validated_provider_set(
    values: frozenset[Provider], *, label: str
) -> frozenset[Provider]:
    providers = frozenset(values)
    if any(not isinstance(provider, Provider) for provider in providers):
        raise WorkloadPolicyError(f"{label} must contain only Provider values")
    return providers


def _direct_candidate_routing_rank(
    *,
    assignment_id: str,
    provider: Provider,
    kind: WorkKind,
    effort_units: int,
    history: tuple[DirectProductionRecord, ...],
    policy: WorkloadPolicy,
) -> int | None:
    """Return the soft-allocation tier, or ``None`` when the hard cap blocks."""

    if not is_accepted_productive_kind(provider, kind):
        return 1
    direct_provider = direct_production_provider(provider)
    if direct_provider is None:
        return None
    prior = history[-(policy.rolling_window_size - 1) :]
    if any(record.assignment_id == assignment_id for record in prior):
        raise WorkloadPolicyError(
            "trusted provider selection assignment id collides with direct history"
        )
    window = prior + (
        DirectProductionRecord(
            assignment_id=assignment_id,
            provider=direct_provider,
            effort_units=effort_units,
        ),
    )
    total_units = sum(record.effort_units for record in window)
    provider_units = sum(
        record.effort_units
        for record in window
        if record.provider is direct_provider
    )
    if (
        len(window) == policy.rolling_window_size
        and provider_units * 100
        > policy.direct_production_hard_threshold(direct_provider) * total_units
    ):
        return None

    soft_floor, soft_ceiling = policy.direct_production_soft_band(direct_provider)
    if provider_units * 100 < soft_floor * total_units:
        return 0
    if provider_units * 100 > soft_ceiling * total_units:
        return 2
    return 1


def _total_effort_candidate_routing_rank(
    *,
    assignment_id: str,
    provider: Provider,
    effort_units: int,
    history: tuple[WorkloadRecord, ...],
    policy: WorkloadPolicy,
) -> int | None:
    """Return prospective total-effort preference, blocking only new overage.

    Recorded history is immutable and remains attributed to the provider that
    actually consumed it.  A candidate never turns that effort into accepted
    production.  With no observed history there is no denominator from which to
    infer an overage, so the first bounded assignment is allowed.
    """

    prior = history[-(policy.rolling_window_size - 1) :]
    if len(prior) < policy.rolling_window_size - 1:
        return 1
    window = prior + (WorkloadRecord(assignment_id, provider, effort_units),)
    total_units = sum(record.effort_units for record in window)
    provider_units = sum(
        record.effort_units for record in window if record.provider is provider
    )
    if provider_units * 100 > policy.overall_hard_ceiling(provider) * total_units:
        return None
    normal_min, normal_max = policy.overall_normal_band(provider)
    if provider_units * 100 < normal_min * total_units:
        return 0
    if provider_units * 100 > normal_max * total_units:
        return 2
    return 1


def select_cheapest_capable_provider(
    *,
    assignment_id: str,
    kind: WorkKind,
    effort_units: int,
    trusted_profiles: Sequence[TrustedProviderProfile],
    ready_providers: frozenset[Provider],
    in_scope_providers: frozenset[Provider],
    reviewer_independent_providers: frozenset[Provider],
    direct_production_history: Sequence[DirectProductionRecord] = (),
    overall_effort_history: Sequence[WorkloadRecord] = (),
    explicit_required_provider: Provider | None = None,
    policy: WorkloadPolicy = DEFAULT_POLICY,
) -> Provider:
    """Select deterministically from trusted facts without rewriting an assignment.

    A trusted explicit provider requirement wins only after capability, readiness,
    scope, reviewer-independence, and direct hard-limit checks. Otherwise direct
    providers below the target-minus-three soft band are preferred, followed by
    providers inside the band, then providers above target-plus-three; trusted
    cost rank and provider id break ties. Missing trusted facts fail closed.

    Callers must construct every profile and provider set from authenticated
    configuration/runtime evidence. Model-authored capability, cost, availability,
    scope, reviewer-binding, or explicit-provider claims are not valid inputs.
    """

    if not isinstance(assignment_id, str) or not assignment_id.strip():
        raise WorkloadPolicyError(
            "trusted provider selection assignment id must be a non-empty string"
        )
    if not isinstance(kind, WorkKind):
        raise WorkloadPolicyError(
            "trusted provider selection kind must be a WorkKind"
        )
    _positive_int(effort_units, "trusted provider selection effort units")
    profiles = tuple(trusted_profiles)
    if not profiles or any(
        not isinstance(profile, TrustedProviderProfile) for profile in profiles
    ):
        raise WorkloadPolicyError(
            "trusted provider profiles are required for provider selection"
        )
    profile_providers = [profile.provider for profile in profiles]
    if len(profile_providers) != len(set(profile_providers)):
        raise WorkloadPolicyError("trusted provider profiles must be unique")
    ready = _validated_provider_set(ready_providers, label="ready providers")
    in_scope = _validated_provider_set(
        in_scope_providers, label="in-scope providers"
    )
    reviewer_independent = _validated_provider_set(
        reviewer_independent_providers,
        label="reviewer-independent providers",
    )
    direct_history = tuple(direct_production_history)
    if any(
        not isinstance(record, DirectProductionRecord) for record in direct_history
    ):
        raise WorkloadPolicyError(
            "direct-production history must contain only DirectProductionRecord values"
        )
    direct_assignment_ids = [record.assignment_id for record in direct_history]
    if len(direct_assignment_ids) != len(set(direct_assignment_ids)):
        raise WorkloadPolicyError(
            "trusted provider selection requires unique direct-production credit ids"
        )
    if explicit_required_provider is not None and not isinstance(
        explicit_required_provider, Provider
    ):
        raise WorkloadPolicyError(
            "trusted explicit provider requirement must be absent or a Provider"
        )

    overall_history = tuple(overall_effort_history)
    if any(not isinstance(record, WorkloadRecord) for record in overall_history):
        raise WorkloadPolicyError(
            "overall-effort history must contain only WorkloadRecord values"
        )
    ranked: list[tuple[int, int, int, str, Provider]] = []
    for profile in profiles:
        provider = profile.provider
        if kind not in profile.supported_work_kinds:
            continue
        if provider not in ready or provider not in in_scope:
            continue
        if kind is WorkKind.REVIEW and provider not in reviewer_independent:
            continue
        if kind is WorkKind.IMPLEMENTATION and not any(
            candidate.provider is not provider
            and WorkKind.REVIEW in candidate.supported_work_kinds
            and candidate.provider in ready
            and candidate.provider in reviewer_independent
            for candidate in profiles
        ):
            continue
        direct_rank = _direct_candidate_routing_rank(
            assignment_id=assignment_id,
            provider=provider,
            kind=kind,
            effort_units=effort_units,
            history=direct_history,
            policy=policy,
        )
        if direct_rank is None:
            continue
        total_effort_rank = _total_effort_candidate_routing_rank(
            assignment_id=assignment_id,
            provider=provider,
            effort_units=effort_units,
            history=overall_history,
            policy=policy,
        )
        if total_effort_rank is None:
            continue
        ranked.append(
            (
                total_effort_rank,
                direct_rank,
                profile.trusted_cost_rank,
                provider.value,
                provider,
            )
        )

    if explicit_required_provider is not None:
        for _total_rank, _direct_rank, _cost_rank, _provider_id, provider in ranked:
            if provider is explicit_required_provider:
                return provider
        raise WorkloadPolicyError(
            "trusted explicit provider requirement is not eligible for this assignment"
        )
    if not ranked:
        raise WorkloadPolicyError(
            "no trusted capable, ready, in-scope, independent, allocation-eligible provider"
        )
    return min(ranked)[-1]


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

    Total-effort history is a separate prospective usage guard; it may route or
    block future dispatch but cannot create accepted-production credit.  The
    accepted-production ledger remains independently enforced.
    """

    if not isinstance(mode, EnforcementMode):
        raise WorkloadPolicyError("mode must be an EnforcementMode")
    reported_records = tuple(history)
    overall_history_invalid = any(
        not isinstance(record, WorkloadRecord) for record in reported_records
    )
    records = tuple(
        record
        for record in reported_records
        if isinstance(record, WorkloadRecord)
    )
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
                f"primary_team_alternatives_available:{len(other_available)}"
            )
    if (
        request.provider is Provider.CLAUDE
        and request.kind is WorkKind.IMPLEMENTATION
        and request.size in {
            WorkSize.MEDIUM,
            WorkSize.LONG,
        }
    ):
        if request.model_label not in policy.approved_claude_model_labels:
            violations.append(
                "claude_medium_long_implementation_requires_model_floor:"
                f"{policy.claude_model_floor}"
            )

    # Total effort preserves actual provider attribution and controls only the
    # next dispatch. Invalid identity is ignored rather than allowed to invent a
    # block or production credit.
    advisories.append("total_effort_prospective_usage_guard")
    if overall_history_invalid:
        advisories.append("overall_effort_history_invalid_records_ignored")
    prior = records[-(policy.rolling_window_size - 1) :]
    candidate = WorkloadRecord(
        assignment_id=request.assignment_id,
        provider=request.provider,
        effort_units=request.effort_units,
    )
    window = prior + (candidate,)
    ids = [record.assignment_id for record in window]
    if len(ids) != len(set(ids)):
        advisories.append("overall_effort_assignment_ids_not_unique")

    if len(window) < policy.rolling_window_size:
        advisories.append(
            f"rolling_window_warming_up:{len(window)}/{policy.rolling_window_size}"
        )
    else:
        total_units = sum(record.effort_units for record in window)
        requested_provider_units = sum(
            record.effort_units
            for record in window
            if record.provider is request.provider
        )
        advisories.append(
            "prospective_rolling_share:"
            f"{request.provider.value}:{requested_provider_units}/{total_units}"
        )
    if len(window) == policy.rolling_window_size:
        total_units = sum(record.effort_units for record in window)
        requested_provider_units = sum(
            record.effort_units
            for record in window
            if record.provider is request.provider
        )
        ceiling = policy.overall_hard_ceiling(request.provider)
        normal_min, normal_max = policy.overall_normal_band(request.provider)
        if requested_provider_units * 100 > ceiling * total_units:
            violations.append(
                "total_effort_hard_ceiling_exceeded:"
                f"{request.provider.value}:{ceiling}"
            )
        elif requested_provider_units * 100 < normal_min * total_units:
            advisories.append(
                "total_effort_provider_under_normal_minimum:"
                f"{request.provider.value}:{normal_min}"
            )
        elif requested_provider_units * 100 > normal_max * total_units:
            advisories.append(
                "total_effort_provider_above_normal_band:"
                f"{request.provider.value}:{normal_max}"
            )
    direct_provider = direct_production_provider(request.provider)
    if direct_provider is not None and is_accepted_productive_kind(
        request.provider, request.kind
    ):
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
            requested_direct_provider_units = sum(
                record.effort_units
                for record in direct_window
                if record.provider is direct_provider
            )
            advisories.append(
                "prospective_direct_production_share:"
                f"{direct_provider.value}:"
                f"{requested_direct_provider_units}/{direct_total}"
            )
            soft_floor, soft_ceiling = policy.direct_production_soft_band(
                direct_provider
            )
            if requested_direct_provider_units * 100 < soft_floor * direct_total:
                advisories.append(
                    "direct_production_below_soft_band:"
                    f"{direct_provider.value}:{soft_floor}"
                )
            elif (
                requested_direct_provider_units * 100
                > soft_ceiling * direct_total
            ):
                advisories.append(
                    "direct_production_above_soft_band:"
                    f"{direct_provider.value}:{soft_ceiling}"
                )
            for provider in DirectProductionProvider:
                provider_units = sum(
                    record.effort_units
                    for record in direct_window
                    if record.provider is provider
                )
                if provider_units == 0:
                    continue
                direct_threshold = policy.direct_production_hard_threshold(
                    provider
                )
                if provider_units * 100 <= direct_threshold * direct_total:
                    continue
                if provider is direct_provider:
                    violations.append(
                        "direct_production_hard_threshold_exceeded:"
                        f"{provider.value}:{direct_threshold}"
                    )
                else:
                    advisories.append(
                        "direct_production_other_provider_over_hard_threshold:"
                        f"{provider.value}:{direct_threshold}"
                    )

    return _decision(mode, violations, advisories)


@dataclass(frozen=True)
class RetryRequest:
    attempt_number: int
    retry_limit: int
    previous_failure: FailureKind = FailureKind.NONE
    transport: Transport = Transport.DIRECTOR_ADAPTER
    previous_transport: Transport | None = None
    materially_new_information: bool = False
    corrected_approach: bool = False
    positive_expected_information_gain: bool = False
    token_budget_remaining: bool = True
    cost_budget_remaining: bool = True
    timeout_budget_remaining: bool = True
    cancellation_requested: bool = False

    def __post_init__(self) -> None:
        _positive_int(self.attempt_number, "attempt number")
        _positive_int(self.retry_limit, "assignment retry limit")
        if self.retry_limit > 3:
            raise WorkloadPolicyError("assignment retry limit cannot exceed 3 attempts")
        if not isinstance(self.previous_failure, FailureKind):
            raise WorkloadPolicyError("previous failure must be a FailureKind")
        if not isinstance(self.transport, Transport):
            raise WorkloadPolicyError("transport must be a Transport")
        if self.previous_transport is not None and not isinstance(
            self.previous_transport, Transport
        ):
            raise WorkloadPolicyError("previous transport must be absent or a Transport")
        for value, label in (
            (self.materially_new_information, "materially new information"),
            (self.corrected_approach, "corrected approach"),
            (self.positive_expected_information_gain, "positive expected information gain"),
            (self.token_budget_remaining, "token budget remaining"),
            (self.cost_budget_remaining, "cost budget remaining"),
            (self.timeout_budget_remaining, "timeout budget remaining"),
            (self.cancellation_requested, "cancellation requested"),
        ):
            if not isinstance(value, bool):
                raise WorkloadPolicyError(f"{label} must be boolean")


def evaluate_retry(
    request: RetryRequest,
    *,
    policy: WorkloadPolicy = DEFAULT_POLICY,
    mode: EnforcementMode = EnforcementMode.ENFORCED,
) -> DecisionResult:
    """Enforce the immutable assignment budget and evidence-gain retry gate."""

    violations: list[str] = []
    advisories: list[str] = []
    if request.transport not in policy.enabled_transports:
        violations.append("transport_disabled")
    elif request.transport is Transport.BROWSER:
        advisories.append("browser_transport_enabled_at_lowest_priority")

    if request.cancellation_requested:
        violations.append("assignment_cancelled")
    if not request.token_budget_remaining:
        violations.append("token_budget_exhausted")
    if not request.cost_budget_remaining:
        violations.append("cost_budget_exhausted")
    if not request.timeout_budget_remaining:
        violations.append("timeout_budget_exhausted")

    if request.attempt_number > request.retry_limit:
        violations.append("retry_limit_exceeded")
    elif request.attempt_number == 1:
        if request.previous_failure is not FailureKind.NONE:
            violations.append("primary_attempt_must_not_have_previous_failure")
    elif request.previous_failure is FailureKind.AMBIGUOUS_PAID_DISPATCH:
        violations.append("ambiguous_paid_dispatch_must_not_be_repeated")
    else:
        if request.previous_failure is FailureKind.NONE:
            violations.append("additional_attempt_requires_classified_failure")
        transport_changed = (
            request.previous_transport is not None
            and request.previous_transport is not request.transport
        )
        changed_basis = (
            request.materially_new_information
            or request.corrected_approach
            or transport_changed
        )
        if not request.positive_expected_information_gain or not changed_basis:
            violations.append(
                "additional_attempt_requires_positive_information_gain_and_changed_basis"
            )

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

        path_collection_names = ("owned_paths", "prohibited_paths")
        for name in path_collection_names:
            raw = tuple(getattr(self, name))
            normalized = tuple(
                _canonical_repository_relative_path(item, field_name=name)
                for item in raw
            )
            aliases = [item.casefold() for item in normalized]
            if len(set(aliases)) != len(aliases):
                raise WorkloadPolicyError(
                    f"prestage {name} must not contain duplicates"
                )
            object.__setattr__(self, name, tuple(sorted(normalized)))

        collection_names = (
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
        for owned in self.owned_paths:
            for prohibited in self.prohibited_paths:
                if _repository_path_scopes_overlap(owned, prohibited):
                    raise WorkloadPolicyError(
                        "prestage owned and prohibited paths must not overlap"
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


_WINDOWS_DRIVE_PATH = re.compile(r"^[a-zA-Z]:")


def _canonical_repository_relative_path(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkloadPolicyError(
            f"prestage {field_name} must contain only non-blank strings"
        )
    candidate = value.strip().replace("\\", "/")
    if candidate.startswith("/") or _WINDOWS_DRIVE_PATH.match(candidate):
        raise WorkloadPolicyError(
            f"prestage {field_name} paths must be repository-relative"
        )
    parts: list[str] = []
    for part in candidate.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            raise WorkloadPolicyError(
                f"prestage {field_name} paths must be repository-relative"
            )
        if any(character in part for character in "*?[]"):
            raise WorkloadPolicyError(
                f"prestage {field_name} paths must be literal repository-relative paths"
            )
        if part.endswith((" ", ".")):
            raise WorkloadPolicyError(
                f"prestage {field_name} paths must be portable repository-relative paths"
            )
        parts.append(part)
    if not parts:
        raise WorkloadPolicyError(
            f"prestage {field_name} paths must be repository-relative"
        )
    return "/".join(parts)


def _repository_path_scopes_overlap(left: str, right: str) -> bool:
    left_parts = tuple(part.casefold() for part in left.split("/"))
    right_parts = tuple(part.casefold() for part in right.split("/"))
    common = min(len(left_parts), len(right_parts))
    return left_parts[:common] == right_parts[:common]


# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 03:56 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 05:39 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 06:29 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 04:15 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 02:44 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:18 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:14 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | END]]
