# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-12 | 14:05 UTC | BEGIN]]
# [[OpenAI Codex queue_runtime author | GPT-6 Astra | 2026-09-12 | 11:23 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 09:30 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 09:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:46 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-10 | 18:17 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-10 | 16:46 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 18:30 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 04:06 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 07:16 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:40 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:37 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:12 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:56 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 10:07 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:18 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:11 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:22 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:20 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 01:02 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:14 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 03:05 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:46 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:19 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:46 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 07:30 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 07:05 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:40 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:28 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:12 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:40 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:32 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:44 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:42 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:17 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:24 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 22:55 UTC | BEGIN]]
"""Default Director application boundary for the paid-off primary rollout."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from .config import Settings
from .antigravity_team_routing import PrimaryProviderBudgetRouting, live_provider_admissions
from .langgraph_postgres import EffectReservation, PrimaryTaskProjection
from .langgraph_postgres import PostgresCheckpointHandle, open_postgres_control_plane
from .langgraph_runtime import (
    PacketBoundaryAdapter,
    ProviderAdapterContext,
    RuntimeExecutionOutcome,
    RuntimeExecutionStatus,
)
from .langgraph_primary import (
    AcceptanceDecision,
    AcceptanceResult,
    ApprovalDecision,
    ApprovalResume,
    CompletionGateResult,
    EffectDisposition,
    EffectExecutionResult,
    EvidenceDebateResult,
    ExactShaReviewResult,
    HandlerContext,
    PhaseResult,
    PlanResult,
    PrimaryHandlers,
    PrimaryRequest,
    ProviderDisposition,
    ProviderSubgraphResult,
    ScheduleResult,
    _context as _handler_context_from_state,
)
from .models import (
    AntigravityDirective,
    AntigravityFixtureLane,
    Assignment,
    AssignmentKind,
    AssignmentOutcome,
    IntakeAuthentication,
    ProviderName,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from .packet_contracts import document_digest, validate_packet
from .packet_routing import DirectorPacketTaskRouter
from .packet_transports import (
    LocalGitAuthoritySnapshotResolver,
    canonical_github_remote_repository,
)
from .policy import preview_plan, _canonical_prestage_record
from .primary_coordinator import PrimaryTaskCoordinator
from .primary_effects import (
    DeterministicExternalEffectExecutor,
    EffectMutation,
    EffectReadback,
)
from .primary_effect_gateways import (
    ExactEffectGatewayRegistry,
    GITHUB_MARK_READY,
    PostgresTypedEffectIntentStore,
    SLACK_POST_MESSAGE,
    TypedEffectIntent,
    build_configured_effect_gateway_registry,
)
from .primary_completion import (
    CompletionEvidence,
    CompletionEvidenceResolver,
    ExternalEffectResolver,
    PacketResultBinding,
    PacketResultResolver,
    ExternalEffectEvidence,
)
from .primary_provider_activation import PrimaryProviderActivationService
from .host_provider_bridge import (
    HostProviderBridgeError,
    challenge_host_provider_bridge,
)
from .primary_providers import CandidateShaResolver, PrimaryProviderBinding
from .primary_parallel import (
    CancelledPlanBindingHold,
    ParallelAggregate,
    ParallelAssignment,
    ParallelAssignmentStatus,
    ParallelDispatchOutcome,
    ParallelDispatchPolicy,
    ParallelReservation,
    PostgresParallelDispatch,
    ProviderAttemptReservation,
    PROVIDER_ATTEMPT_BUDGET_EXHAUSTED,
    PROVIDER_RETRY_REQUIRES_TRANSIENT_FAILURE,
    PROVIDER_TRANSIENT_TRANSPORT_FAILURE,
    authorize_provider_retry,
    classify_provider_dispatch_exception,
    classify_provider_result_failure,
    deterministic_format_roster,
)
from .workload_policy import FailureKind
from .primary_transports import (
    build_graph_native_packet_adapters,
    discover_local_primary_command_transports,
)


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _execute_three_attempt_roster(
    *,
    format_roster: tuple[tuple[str, str], ...],
    reserve_attempt: Callable[[int], ProviderAttemptReservation],
    record_attempt: Callable[
        [ProviderAttemptReservation, ParallelAssignmentStatus, str | None, str],
        None,
    ],
    dispatch_attempt: Callable[[ProviderAttemptReservation], Mapping[str, Any]],
) -> tuple[ParallelAssignmentStatus, Mapping[str, Any] | None, str | None, str]:
    """Run up to three bounded slots; stop after authenticated success."""

    if not 1 <= len(format_roster) <= 3:
        raise RuntimeError("provider format roster must contain one to three entries")
    last_digest: str | None = None
    for attempt_ordinal in range(1, 4):
        attempt = reserve_attempt(attempt_ordinal)
        expected = format_roster[(attempt_ordinal - 1) % len(format_roster)]
        if (attempt.format_id, attempt.transport_id) != expected:
            raise RuntimeError("reserved provider format order changed")
        try:
            result = dispatch_attempt(attempt)
        except BaseException as exc:
            failure_kind = classify_provider_dispatch_exception(exc)
            if failure_kind is FailureKind.TRANSIENT:
                record_attempt(
                    attempt,
                    ParallelAssignmentStatus.FAILED,
                    None,
                    PROVIDER_TRANSIENT_TRANSPORT_FAILURE,
                )
                if attempt_ordinal >= 3:
                    return (
                        ParallelAssignmentStatus.FAILED,
                        None,
                        last_digest,
                        PROVIDER_ATTEMPT_BUDGET_EXHAUSTED,
                    )
                next_transport_id = format_roster[attempt_ordinal % len(format_roster)][1]
                authorized, stop_reason = authorize_provider_retry(
                    attempt_number=attempt_ordinal + 1,
                    previous_failure=failure_kind,
                    transport_id=next_transport_id,
                    previous_transport_id=attempt.transport_id,
                )
                if not authorized:
                    return (
                        ParallelAssignmentStatus.FAILED,
                        None,
                        last_digest,
                        stop_reason,
                    )
                continue
            record_attempt(
                attempt,
                ParallelAssignmentStatus.AMBIGUOUS,
                None,
                "provider_outcome_unknown",
            )
            return (
                ParallelAssignmentStatus.AMBIGUOUS,
                None,
                None,
                "provider_outcome_unknown",
            )
        last_digest = document_digest(result)
        if result["status"] == "SUCCEEDED":
            record_attempt(
                attempt,
                ParallelAssignmentStatus.SUCCEEDED,
                last_digest,
                "provider_result_authenticated",
            )
            return (
                ParallelAssignmentStatus.SUCCEEDED,
                result,
                last_digest,
                "provider_result_authenticated",
            )
        failure_kind = classify_provider_result_failure(result)
        record_attempt(
            attempt,
            ParallelAssignmentStatus.FAILED,
            last_digest,
            "provider_reported_non_success",
        )
        if attempt_ordinal >= 3:
            return (
                ParallelAssignmentStatus.FAILED,
                None,
                last_digest,
                PROVIDER_ATTEMPT_BUDGET_EXHAUSTED,
            )
        if failure_kind is not FailureKind.TRANSIENT:
            return (
                ParallelAssignmentStatus.FAILED,
                None,
                last_digest,
                PROVIDER_RETRY_REQUIRES_TRANSIENT_FAILURE,
            )
        next_transport_id = format_roster[attempt_ordinal % len(format_roster)][1]
        authorized, stop_reason = authorize_provider_retry(
            attempt_number=attempt_ordinal + 1,
            previous_failure=failure_kind,
            transport_id=next_transport_id,
            previous_transport_id=attempt.transport_id,
        )
        if not authorized:
            return (
                ParallelAssignmentStatus.FAILED,
                None,
                last_digest,
                stop_reason,
            )
    return (
        ParallelAssignmentStatus.FAILED,
        None,
        last_digest,
        PROVIDER_ATTEMPT_BUDGET_EXHAUSTED,
    )


def _primary_provider_lane(provider: ProviderName) -> str:
    """Map only the typed Gemini assignment backend to its Antigravity lane."""

    return "antigravity" if provider == ProviderName.GEMINI else provider.value


_PRIMARY_LANE_AGENT: Mapping[str, ProviderName] = {
    "openai": ProviderName.OPENAI,
    "claude": ProviderName.CLAUDE,
    "cursor": ProviderName.CURSOR,
    "grok": ProviderName.GROK,
    "antigravity": ProviderName.GEMINI,
    "perplexity": ProviderName.PERPLEXITY,
    "firecrawl": ProviderName.FIRECRAWL,
    "github-copilot": ProviderName.GITHUB_COPILOT,
}
_PRIMARY_PUBLIC_RESEARCH_LANES = frozenset(
    {"perplexity", "firecrawl"}
)


def _assignment_provider_lane(assignment: Assignment) -> str:
    return assignment.primary_provider_lane or _primary_provider_lane(assignment.agent)


GITHUB_MARK_READY_EFFECT_KIND = "github_mark_ready"
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_GITHUB_EFFECT_TARGET = re.compile(
    r"^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#([1-9][0-9]*)$"
)


@dataclass(frozen=True, slots=True)
class TypedExternalEffectGrant:
    """The exact typed external effect the coordinator authorizes for one task.

    The grant is built from Director configuration and already-verified
    repository authority before dispatch, never from provider output. Owner
    approval digests it, the work packet grants its kind, and the executed
    intent must reproduce it coordinate for coordinate.
    """

    kind: str
    system: str
    action: str
    repository: str
    pull_number: int
    base_sha: str
    head_sha: str

    def __post_init__(self) -> None:
        if self.kind != GITHUB_MARK_READY_EFFECT_KIND:
            raise ValueError("typed external effect grant kind is unsupported")
        if not self.repository or self.repository.count("/") != 1:
            raise ValueError("typed external effect grant repository is malformed")
        if type(self.pull_number) is not int or self.pull_number < 1:
            raise ValueError("typed external effect grant pull number is malformed")
        for value in (self.base_sha, self.head_sha):
            if _SHA40.fullmatch(value) is None:
                raise ValueError("typed external effect grant SHA is malformed")

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "system": self.system,
            "action": self.action,
            "repository": self.repository,
            "pull_number": self.pull_number,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
        }

    @property
    def target(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "pull_number": self.pull_number,
            "head_sha": self.head_sha,
            "base_sha": self.base_sha,
        }

    @property
    def proposal_target(self) -> str:
        return f"{self.repository}#{self.pull_number}"


def typed_external_effect_grant_kinds(settings: Settings) -> tuple[str, ...]:
    """Return the effect kinds the coordinator can grant under this config."""

    if len(settings.repository_allowlist) != 1:
        return ()
    if int(settings.langgraph_primary_external_effect_pull_number) < 1:
        return ()
    if any(_SHA40.fullmatch(getattr(settings, name, "")) is None for name in (
        "langgraph_primary_external_effect_base_sha", "langgraph_primary_external_effect_head_sha")):
        return ()
    return (GITHUB_MARK_READY_EFFECT_KIND,)


def build_typed_external_effect_grant(
    settings: Settings, authority: DefaultPrimaryAuthority
) -> TypedExternalEffectGrant | None:
    """Build the coordinator's exact typed grant, or none when unconfigured."""

    if not typed_external_effect_grant_kinds(settings):
        return None
    pull_number = int(settings.langgraph_primary_external_effect_pull_number)
    repository = settings.repository_allowlist[0].removesuffix(".git").strip("/")
    base_sha = settings.langgraph_primary_external_effect_base_sha
    head_sha = settings.langgraph_primary_external_effect_head_sha
    if _SHA40.fullmatch(base_sha) is None or _SHA40.fullmatch(head_sha) is None:
        return None
    return TypedExternalEffectGrant(
        kind=GITHUB_MARK_READY_EFFECT_KIND,
        system="github",
        action=GITHUB_MARK_READY,
        repository=repository,
        pull_number=pull_number,
        # These owner-controlled PR coordinates enter the approval digest and
        # are revalidated remotely by the gateway; deployment is a separate SHA.
        base_sha=base_sha,
        head_sha=head_sha,
    )


def _terminal_review_assignment(record: TaskRecord) -> Assignment | None:
    """Return the plan's exact terminal review barrier, or nothing.

    The barrier is identified structurally - the highest-sequence independent
    review that depends on every other plan member - never by provider lane. A
    reviewer is independently selected; structure binds the complete evidence
    barrier even for older plans that reused a first-wave lane.
    """

    plan = record.plan
    if plan is None or not plan.assignments:
        return None
    review = max(plan.assignments, key=lambda item: item.sequence)
    if review.kind is not AssignmentKind.INDEPENDENT_REVIEW:
        return None
    remaining = {
        item.sequence for item in plan.assignments if item.sequence != review.sequence
    }
    if set(review.depends_on) != remaining:
        return None
    return review


def _typed_effect_binding(
    *,
    grant: TypedExternalEffectGrant | None,
    record: TaskRecord,
    context: HandlerContext,
    granted_kinds: frozenset[str],
    effect: Mapping[str, Any],
) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    """Bind one proposal to the coordinator's exact typed grant.

    The proposal selects nothing but *which* granted effect is requested. Its
    kind must be granted by the work packet, and its target must match the
    Director-owned grant exactly; every mutation coordinate is then taken from
    the grant, so a redirected repository, pull request, base or head cannot
    reach the gateway.
    """

    kind = str(effect.get("kind", ""))
    target_text = str(effect.get("target", ""))
    if kind not in granted_kinds:
        raise RuntimeError("external effect kind is not granted by the work packet")
    if kind == "slack_post":
        if target_text != record.intake.channel_id:
            raise RuntimeError("Slack effect target changed")
        return (
            "slack",
            SLACK_POST_MESSAGE,
            {
                "channel": record.intake.channel_id,
                "thread_ts": record.intake.thread_ts,
            },
            {
                "text": (
                    f"Director task {context.task_id} completed at "
                    f"{context.candidate_sha}."
                )
            },
        )
    if kind == GITHUB_MARK_READY_EFFECT_KIND:
        if grant is None:
            raise RuntimeError(
                "GitHub effect requires one allowlisted repository and a "
                "configured positive pull request"
            )
        match = _GITHUB_EFFECT_TARGET.fullmatch(target_text)
        if match is None:
            raise RuntimeError("GitHub effect target is malformed")
        if match.group(1).removesuffix(".git").lower() != grant.repository.lower():
            raise RuntimeError(
                "GitHub effect target is outside the repository allowlist"
            )
        if int(match.group(2)) != grant.pull_number:
            raise RuntimeError(
                "GitHub effect target is not the configured pull request"
            )
        return ("github", GITHUB_MARK_READY, grant.target, {})
    raise RuntimeError("external effect action is unsupported")


def _live_parallel_plan(record: TaskRecord, settings: Settings, *,
                        available: tuple[str, ...], history: tuple[str, ...]) -> None:
    """Freeze a minimal capability-selected DAG before execution approval."""

    if record.plan is None or not record.plan.assignments:
        raise RuntimeError("live primary parallel expansion requires a normalized plan")
    from .primary_roster import select_primary_roster
    from .allocation_profiles import canonical_allocation_lane
    selected = select_primary_roster(available=available, history=history,
        required_capabilities=record.intake.primary_required_capabilities,
        public_research_query=record.intake.primary_public_research_query,
        trusted_cost_ranks={canonical_allocation_lane(lane): rank for lane, rank in settings.trusted_provider_cost_ranks().items()},
        allocation_profile=record.intake.allocation_profile,
        team_mode=settings.langgraph_primary_antigravity_team_enabled)
    required = ((selected.research,) if selected.research else ()) + (selected.producer,)
    # The paid-off preview always declares a zero cost. A live wave must carry a
    # trusted positive per-lane limit derived from the configured task budget so
    # the packet, the dispatcher and the transport all enforce the same ceiling.
    assignment_count = len(required) + 1
    task_budget_usd = float(settings.max_task_budget_usd)
    lane_budget_usd = task_budget_usd / assignment_count
    if task_budget_usd <= 0.0 or lane_budget_usd <= 0.0:
        raise RuntimeError(
            "live primary dispatch requires a positive configured task budget"
        )

    plan = record.plan
    work_template = min(plan.assignments, key=lambda item: item.sequence)
    review_template = max(plan.assignments, key=lambda item: item.sequence)
    repository = plan.prestage.repository if plan.prestage is not None else work_template.repository
    starting_ref = work_template.starting_ref
    first_wave: list[Assignment] = []
    for sequence, lane in enumerate(required, start=1):
        is_research = lane in _PRIMARY_PUBLIC_RESEARCH_LANES
        is_antigravity = lane == "antigravity"
        first_wave.append(
            work_template.model_copy(
                update={
                    "sequence": sequence,
                    "agent": _PRIMARY_LANE_AGENT[lane],
                    "primary_provider_lane": lane,
                    "allocation_profile": record.intake.allocation_profile,
                    "kind": (
                        AssignmentKind.SCAFFOLDING
                        if is_antigravity
                        else AssignmentKind.RESEARCH
                        if is_research
                        else AssignmentKind.GENERAL
                    ),
                    "task": (
                        "Produce a bounded generic module skeleton for independent integration."
                        if is_antigravity
                        else record.intake.primary_public_research_query
                        if is_research
                        else record.intake.text.strip()
                    ),
                    "acceptance": [
                        "Return bounded evidence for the assigned lane",
                        "Report blockers and unresolved risks without external effects",
                    ],
                    "public_research_query": (
                        record.intake.primary_public_research_query
                        if is_research
                        else None
                    ),
                    "antigravity_directive": (
                        AntigravityDirective(
                            lane=AntigravityFixtureLane.MODULE_SKELETON
                        )
                        if is_antigravity
                        else None
                    ),
                    "repository": None if is_research or is_antigravity else repository,
                    "starting_ref": starting_ref,
                    "depends_on": ([1] if selected.research and lane == selected.producer else []),
                    "requires_approval": is_research,
                    "allow_draft_pr": False,
                    "retry_limit": 3,
                    "estimated_cost_usd": lane_budget_usd,
                }
            )
        )
    review_lane = selected.reviewer
    review = review_template.model_copy(
        update={
            "sequence": len(first_wave) + 1,
            "primary_provider_lane": review_lane,
            "allocation_profile": record.intake.allocation_profile,
            "agent": _PRIMARY_LANE_AGENT[review_lane],
            "kind": AssignmentKind.INDEPENDENT_REVIEW,
            "task": (
                "Review the complete first-wave provider evidence, identify conflicts, "
                "and produce the bounded synthesis for graph-owned acceptance."
            ),
            "acceptance": [
                "Every first-wave result is accounted for",
                "Contradictions, missing evidence, and unresolved risks are explicit",
            ],
            "repository": repository,
            "public_research_query": None,
            "antigravity_directive": None,
            "depends_on": [item.sequence for item in first_wave],
            "requires_approval": False,
            "allow_draft_pr": False,
            "retry_limit": 3,
            "estimated_cost_usd": lane_budget_usd,
        }
    )
    record.plan = plan.model_copy(update={"assignments": [*first_wave, review]})
    if record.plan.prestage is not None:
        record.plan.prestage = _canonical_prestage_record(record.plan.prestage.model_copy(update={
            "allocation_profile": record.intake.allocation_profile,
            "producer": _PRIMARY_LANE_AGENT[selected.producer],
            "independent_reviewer": _PRIMARY_LANE_AGENT[selected.reviewer],
            "digest": None,
        }))


def _bounded_secret_file(path_value: str) -> str:
    if not path_value:
        return ""
    path = Path(path_value).resolve(strict=True)
    if not path.is_file() or path.stat().st_size > 65_536:
        raise ValueError("delegated token cache reference is not a bounded file")
    value = path.read_text(encoding="utf-8").strip()
    if not value or "\n" in value or "\r" in value:
        raise ValueError("delegated token cache contains an invalid bearer")
    return value


@dataclass(frozen=True, slots=True)
class DefaultPrimaryAuthority:
    """One already-verified repository authority used for a new intake."""

    starting_sha: str
    authority_snapshot_digest: str


@dataclass(frozen=True, slots=True)
class DefaultPrimaryLiveDependencies:
    """Explicit external dependencies required by the live primary graph."""

    adapters: Mapping[str, PacketBoundaryAdapter]
    candidate_sha_resolver: CandidateShaResolver
    packet_result_resolver: PacketResultResolver
    completion_evidence_resolver: CompletionEvidenceResolver
    external_effect_resolver: ExternalEffectResolver
    evidence_debate_handler: Callable[[HandlerContext], EvidenceDebateResult]
    acceptance_handler: Callable[[HandlerContext], AcceptanceResult]
    attempt_formats: Mapping[str, tuple[tuple[str, str], ...]] = field(
        default_factory=dict
    )
    attempt_adapters: Mapping[
        str, Mapping[tuple[str, str], PacketBoundaryAdapter]
    ] = field(default_factory=dict)
    team_routing: Any | None = None
    budget_routing: PrimaryProviderBudgetRouting | None = None

    def format_roster(self, provider: str) -> tuple[tuple[str, str], ...]:
        return deterministic_format_roster(
            self.attempt_formats.get(
                provider,
                (("model-work-packet-v1", "packet-boundary-adapter"),),
            )
        )

    def adapter_for_format(
        self, provider: str, format_transport: tuple[str, str]
    ) -> PacketBoundaryAdapter:
        configured = self.attempt_adapters.get(provider, {})
        if configured:
            try:
                return configured[format_transport]
            except KeyError as exc:
                raise ValueError(
                    f"live primary format transport is unbound for {provider}"
                ) from exc
        return self.adapters[provider]

    def validate(self, required_providers: tuple[str, ...]) -> None:
        from .antigravity_team_routing import PrimaryProviderBudgetRouting
        if not isinstance(self.budget_routing, PrimaryProviderBudgetRouting):
            raise ValueError("live primary requires typed enrolled daily provider budget routing")
        expected = set(required_providers)
        actual = set(self.adapters)
        from .primary_roster import core_inventory_ready
        if not actual.issubset(expected) or not core_inventory_ready(tuple(actual)):
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            details = []
            if missing:
                details.append("missing=" + ",".join(missing))
            if extra:
                details.append("unexpected=" + ",".join(extra))
            raise ValueError(
                "live primary provider adapters must cover core capabilities within configured "
                "providers (" + "; ".join(details) + ")"
            )
        for provider, adapter in self.adapters.items():
            if not isinstance(adapter, PacketBoundaryAdapter):
                raise ValueError(
                    f"live primary adapter for {provider} is not a packet boundary"
                )
            if adapter.provider != provider:
                raise ValueError(
                    f"live primary adapter identity does not match {provider}"
                )
            roster = self.format_roster(provider)
            if not 1 <= len(roster) <= 3:
                raise ValueError(
                    f"live primary attempt formats for {provider} must contain one to three entries"
                )
            for format_transport in roster:
                selected = self.adapter_for_format(provider, format_transport)
                if selected.provider != provider:
                    raise ValueError(
                        f"live primary format adapter identity does not match {provider}"
                    )
        required_callables = {
            "candidate SHA resolver": self.candidate_sha_resolver,
            "durable packet-result resolver": self.packet_result_resolver,
            "durable completion-evidence resolver": self.completion_evidence_resolver,
            "durable external-effect resolver": self.external_effect_resolver,
            "evidence/debate handler": self.evidence_debate_handler,
            "OpenAI acceptance handler": self.acceptance_handler,
        }
        for name, dependency in required_callables.items():
            if not callable(dependency):
                raise ValueError(f"live primary {name} is unavailable")


def build_primary_packet_result_resolver(
    *,
    handle: PostgresCheckpointHandle,
    services: "DefaultDryRunServices",
    parallel: Any | None = None,
) -> PacketResultResolver:
    """Resolve the exact terminal review packet the graph aggregate stands for.

    The live graph resumes its single provider edge with the parallel ledger's
    ``aggregate_digest``, while every packet artifact carries its own individual
    digest. This resolver therefore verifies the aggregate the graph is carrying,
    then reads the terminal review assignment's exact identifier and result
    digest out of that same durable aggregate map. The pair it returns - review
    assignment ID and review result digest - is what evidence, acceptance and
    effects all consume, so nothing downstream can fall back to a producer that
    merely reuses the reviewer's provider lane.
    """

    dispatch = parallel if parallel is not None else PostgresParallelDispatch(
        handle.claim_store
    )

    def packet_result(context: HandlerContext) -> PacketResultBinding | None:
        record = services._record(context.task_id)
        if context.provider_result_digest is None:
            return None
        review = _terminal_review_assignment(record)
        if review is None:
            return None
        assignment_id = services._packet_router._assignment_id(record, review)
        try:
            aggregate = dispatch.aggregate(context.task_id)
        except Exception:
            return None
        graph_result_digest: str | None = None
        review_result_digest = aggregate.result_digests.get(assignment_id)
        if aggregate.aggregate_digest == context.provider_result_digest:
            # The parallel wave: the graph carries the aggregate, and the exact
            # review result digest comes from that same durable map.
            if (
                not aggregate.terminal
                or aggregate.ambiguous_slot_count
                or aggregate.statuses.get(assignment_id) != "SUCCEEDED"
                or not review_result_digest
            ):
                return None
            graph_result_digest = aggregate.aggregate_digest
        elif review_result_digest == context.provider_result_digest:
            # The single-assignment edge: the graph already carries the exact
            # review result digest.
            graph_result_digest = review_result_digest
        else:
            return None
        work = handle.claim_store.read_work_packet_artifact(
            task_id=context.task_id, assignment_id=assignment_id
        )
        result = handle.claim_store.read_result_packet_artifact(
            task_id=context.task_id, assignment_id=assignment_id
        )
        if work is None or result is None:
            return None
        if (
            str(work.packet.get("assignment_id")) != assignment_id
            or str(result.packet.get("assignment_id")) != assignment_id
            or result.document_digest != review_result_digest
        ):
            return None
        packet = dict(result.packet)
        status = (
            RuntimeExecutionStatus.COMPLETED
            if packet.get("status") == "SUCCEEDED"
            else RuntimeExecutionStatus.OWNER_DECISION_REQUIRED
            if packet.get("status") == "OWNER_DECISION_REQUIRED"
            else RuntimeExecutionStatus.REJECTED
        )
        return PacketResultBinding(
            record=record,
            assignment=review,
            work_packet=dict(work.packet),
            outcome=RuntimeExecutionOutcome(
                status=status,
                reason="authenticated_primary_result_artifact",
                result_packet=packet,
                result_packet_digest=result.document_digest,
            ),
            assignment_id=assignment_id,
            graph_result_digest=graph_result_digest,
        )

    return packet_result


def build_automatic_primary_live_dependencies(
    settings: Settings,
    handle: PostgresCheckpointHandle,
    services: "DefaultDryRunServices",
    *, team_routing=None,
) -> DefaultPrimaryLiveDependencies:
    """Discover installed exact lanes and compose graph-owned durable resolvers.

    Authentication comes from configured secret presence or a documented,
    non-secret local status/receipt probe. Native keychain state without such a
    contract is never inferred.
    """

    bridge_secret = settings.langgraph_primary_host_bridge_secret
    if getattr(settings,"langgraph_primary_antigravity_team_enabled",False) and team_routing is None:
        raise ValueError("primary team mode requires explicitly enrolled worker, entitlement and artifact services")
    if bridge_secret and bridge_secret in {
        settings.shared_secret,
        settings.slack_gateway_secret,
        settings.langgraph_primary_adapter_callback_secret,
        settings.langgraph_primary_effect_reconciliation_secret,
    }:
        raise ValueError("host provider bridge secret must be distinct")

    authenticated = {
        "openai": bool(settings.openai_api_key),
        "claude": bool(settings.claude_routine_token),
        "cursor": bool(settings.cursor_api_key),
        "grok": bool(settings.grok_api_key),
        "antigravity": bool(settings.gemini_api_key),
        "perplexity": bool(settings.perplexity_api_key),
        "firecrawl": bool(os.environ.get("FIRECRAWL_API_KEY", "").strip()),
        "github-copilot": bool(
            os.environ.get("COPILOT_GITHUB_TOKEN", "").strip()
            or os.environ.get("GH_TOKEN", "").strip()
            or os.environ.get("GITHUB_TOKEN", "").strip()
        ),
    }
    bridge_challenge = None
    if settings.langgraph_primary_host_bridge_endpoint and bridge_secret:
        try:
            bridge_challenge = challenge_host_provider_bridge(
                settings.langgraph_primary_host_bridge_endpoint,
                bridge_secret,
            )
        except HostProviderBridgeError:
            # Configuration is not liveness or command-map evidence. Discovery
            # remains fail-closed and reports the affected lanes unavailable.
            bridge_challenge = None
    transports, capabilities = discover_local_primary_command_transports(
        cwd=settings.repository_snapshot_root,
        authenticated=authenticated,
        openai_transport=settings.langgraph_primary_openai_transport,
        openai_api_key=settings.openai_api_key,
        openai_model=settings.openai_model,
        openai_reasoning_effort=settings.openai_reasoning_effort,
        perplexity_max_results=settings.perplexity_max_results,
        host_bridge_endpoint=settings.langgraph_primary_host_bridge_endpoint,
        host_bridge_secret=bridge_secret,
        host_bridge_writer_owner_token_digest=(
            "sha256:" + hashlib.sha256(
                settings.langgraph_primary_owner_token.encode("utf-8")
            ).hexdigest()
            if bridge_secret
            else ""
        ),
        host_bridge_challenge=bridge_challenge,

    )
    adapters = build_graph_native_packet_adapters(
        transports={k: v for k, v in transports.items() if k in settings.langgraph_primary_required_providers},
        capabilities={k: v for k, v in capabilities.items() if k in settings.langgraph_primary_required_providers},
        required_providers=settings.langgraph_primary_required_providers,
    )
    if team_routing is not None:
        from .antigravity_team_routing import ParallelOnlyTeamTransport
        adapters=dict(adapters)
        adapters["antigravity"]=PacketBoundaryAdapter("antigravity",ParallelOnlyTeamTransport())
    missing = sorted(set(settings.langgraph_primary_required_providers) - set(adapters))
    from .primary_roster import core_inventory_ready
    if not core_inventory_ready(tuple(adapters)):
        raise ValueError(
            "automatic graph-native live dependencies are unavailable: missing="
            + ",".join(missing)
        )

    def candidate_sha(
        _work_packet: Mapping[str, Any], result_packet: Mapping[str, Any]
    ) -> str:
        if team_routing is not None and result_packet.get("provider")=="antigravity":
            return team_routing.candidate(_work_packet["task_id"],_work_packet["assignment_id"])
        candidates: set[str] = set()
        for artifact in result_packet.get("artifacts", []):
            if not isinstance(artifact, Mapping) or artifact.get("kind") != "git_commit":
                continue
            location = str(artifact.get("location", ""))
            value = location.rsplit("@", 1)[-1]
            if re.fullmatch(r"[0-9a-f]{40}", value):
                candidates.add(value)
        if len(candidates) != 1:
            raise RuntimeError("authenticated result does not bind one exact candidate SHA")
        return next(iter(candidates))

    packet_result = build_primary_packet_result_resolver(
        handle=handle, services=services
    )

    def authenticated_result(context: HandlerContext) -> Mapping[str, Any] | None:
        binding = packet_result(context)
        if binding is None or binding.outcome.result_packet is None:
            return None
        if binding.expected_context_digest != context.provider_result_digest:
            return None
        return binding.outcome.result_packet

    def evidence_from_result(context: HandlerContext) -> EvidenceDebateResult:
        packet = authenticated_result(context)
        digest = _digest(
            {
                "task": context.task_id,
                "candidate_sha": context.candidate_sha,
                "provider_result_digest": context.provider_result_digest,
                "phase": "authenticated_evidence_debate",
            }
        )
        passed = bool(
            packet is not None
            and packet.get("status") == "SUCCEEDED"
            and not packet.get("blockers")
            and not packet.get("requested_decisions")
        )
        return EvidenceDebateResult(
            digest,
            _digest({"evidence_digest": digest, "provider": context.producer_id}),
            context.candidate_sha,
            passed=passed,
            reason_code=(
                "authenticated_provider_evidence_recorded"
                if passed
                else "owner_decision_required"
            ),
        )

    def acceptance_from_result(context: HandlerContext) -> AcceptanceResult:
        packet = authenticated_result(context)
        acceptance = packet.get("acceptance", []) if packet is not None else []
        accepted = bool(
            packet is not None
            and packet.get("status") == "SUCCEEDED"
            and acceptance
            and all(
                isinstance(item, Mapping) and item.get("status") == "PASS"
                for item in acceptance
            )
            and not packet.get("risks")
            and not packet.get("blockers")
            and not packet.get("requested_decisions")
        )
        return AcceptanceResult(
            _digest(
                {
                    "task": context.task_id,
                    "candidate_sha": context.candidate_sha,
                    "provider_result_digest": context.provider_result_digest,
                    "accepted": accepted,
                }
            ),
            context.candidate_sha,
            AcceptanceDecision.ACCEPT if accepted else AcceptanceDecision.REJECT,
            reason_code=(
                "authenticated_result_acceptance_passed"
                if accepted
                else "owner_decision_required"
            ),
        )

    def external_effect(context: HandlerContext) -> ExternalEffectEvidence | None:
        if context.effect_attempt_digest is None or context.effect_key_digest is None:
            return None
        reservation = handle.claim_store.read_effect_reservation(
            task_id=context.task_id,
            effect_attempt_digest=context.effect_attempt_digest,
            owner_token=settings.langgraph_primary_owner_token,
        )
        if reservation is None:
            return None
        binding = handle.claim_store.read_remote_binding(
            reservation_key=reservation.reservation_key,
            owner_token=settings.langgraph_primary_owner_token,
        )
        if binding is None:
            return None
        packet = authenticated_result(context)
        if packet is None:
            return None
        matching = [
            effect
            for effect in packet.get("side_effects", [])
            if isinstance(effect, Mapping)
            and effect.get("status") == "COMPLETED"
            and effect.get("external_id") == binding["remote_identifier"]
        ]
        if len(matching) != 1:
            return None
        return ExternalEffectEvidence(
            effect_key_digest=context.effect_key_digest,
            effect_attempt_digest=context.effect_attempt_digest,
            writer_owner_id=context.writer_owner_id,
            disposition=EffectDisposition.COMPLETED,
            result=dict(matching[0]),
            readback=dict(binding),
            reason_code="authenticated_effect_readback_completed",
        )

    def completion_evidence(context: HandlerContext) -> CompletionEvidence | None:
        artifact = handle.claim_store.read_completion_evidence(task_id=context.task_id)
        if artifact is None:
            return None
        if (
            artifact.candidate_sha != context.candidate_sha
            or artifact.provider_result_digest != context.provider_result_digest
        ):
            return None
        return artifact.evidence

    from .antigravity_team_routing import configured_provider_budget_routing
    budget_routing = configured_provider_budget_routing(settings, handle)
    return DefaultPrimaryLiveDependencies(
        adapters=adapters,
        attempt_formats={
            provider_id: (("model-work-packet-v1", capability.transport_kind),)
            for provider_id, capability in capabilities.items()
            if provider_id in adapters
        },
        attempt_adapters={
            provider_id: {
                ("model-work-packet-v1", capabilities[provider_id].transport_kind): adapter
            }
            for provider_id, adapter in adapters.items()
        },
        candidate_sha_resolver=candidate_sha,
        team_routing=team_routing,
        budget_routing=budget_routing,
        packet_result_resolver=packet_result,
        completion_evidence_resolver=completion_evidence,
        external_effect_resolver=external_effect,
        evidence_debate_handler=evidence_from_result,
        acceptance_handler=acceptance_from_result,
    )


def resolve_default_primary_authority(settings: Settings) -> DefaultPrimaryAuthority:
    """Bind the configured clean repository to its exact origin-backed head."""

    if len(settings.repository_allowlist) != 1:
        raise ValueError("Default primary activation requires exactly one repository")
    if len(settings.allowed_starting_refs) != 1:
        raise ValueError("Default primary activation requires exactly one starting ref")
    root = Path(settings.repository_snapshot_root).resolve(strict=True)

    def git(*arguments: str) -> str:
        completed = subprocess.run(
            ("git", "-c", f"safe.directory={root}", *arguments),
            cwd=root,
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if completed.returncode != 0:
            raise RuntimeError("Default primary repository authority could not be verified")
        return completed.stdout.strip()

    if git("status", "--porcelain=v2", "--untracked-files=all"):
        raise RuntimeError("Default primary repository authority must be clean")
    configured_repository = settings.repository_allowlist[0].removesuffix(".git").lower()
    remote = canonical_github_remote_repository(git("remote", "get-url", "origin"))
    if remote != configured_repository:
        raise RuntimeError("Default primary repository remote does not match its allowlist")
    starting_ref = settings.allowed_starting_refs[0]
    starting_sha = git("rev-parse", "--verify", f"{starting_ref}^{{commit}}")
    pinned_commit = settings.langgraph_primary_pinned_commit_sha.strip().lower()
    pinned_tree = settings.langgraph_primary_pinned_tree_sha.strip().lower()
    if bool(pinned_commit) != bool(pinned_tree) or any(
        re.fullmatch(r"[0-9a-f]{40}", item) is None
        for item in (pinned_commit, pinned_tree)
        if item
    ):
        raise ValueError(
            "Default primary immutable commit and tree pins must be supplied "
            "together as 40-character hexadecimal values"
        )
    if pinned_commit:
        head_sha = git("rev-parse", "--verify", "HEAD^{commit}")
        if head_sha != pinned_commit or starting_sha != pinned_commit:
            raise RuntimeError(
                "Default primary HEAD and starting ref must match the pinned commit"
            )
    else:
        remote_sha = git(
            "rev-parse",
            "--verify",
            f"refs/remotes/origin/{starting_ref.removeprefix('refs/heads/')}^{{commit}}",
        )
        if starting_sha != remote_sha:
            raise RuntimeError("Default primary starting ref does not match origin")
    tree_sha = git("rev-parse", "--verify", f"{starting_sha}^{{tree}}")
    if pinned_tree and tree_sha != pinned_tree:
        raise RuntimeError("Default primary computed tree does not match its pin")
    return DefaultPrimaryAuthority(
        starting_sha=starting_sha,
        authority_snapshot_digest=_digest(
            {
                "repository": configured_repository,
                "starting_ref": starting_ref,
                "starting_sha": starting_sha,
                "tree_sha": tree_sha,
            }
        ),
    )


class DefaultDryRunServices:
    """Bounded real planning services for the paid-off Default cutover.

    The first Default activation deliberately stops at the graph's authenticated
    execution approval interrupt. Provider and external-effect handlers are
    fail-closed and cannot make calls. Raw intake and the owner-visible preview
    live only in the Postgres task projection; graph checkpoints remain digest
    addressed.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        packet_router: DirectorPacketTaskRouter | None = None,
        live_composition: bool = False,
    ) -> None:
        if settings.allow_paid_runs and not live_composition:
            raise ValueError("Default dry-run primary services require paid runs off")
        self.settings = settings
        self._live_composition = live_composition
        self.available_provider_lanes: tuple[str, ...] = ()
        self.provider_lane_ready: Callable[[str], bool] = lambda _provider: False
        if packet_router is None:
            if len(settings.repository_allowlist) != 1:
                raise ValueError("Default packet planning requires one repository")
            packet_router = DirectorPacketTaskRouter(
                authority_snapshots=LocalGitAuthoritySnapshotResolver(
                    settings.repository_allowlist[0],
                    settings.repository_snapshot_root,
                    pinned_commit_sha=settings.langgraph_primary_pinned_commit_sha,
                    pinned_tree_sha=settings.langgraph_primary_pinned_tree_sha,
                    max_files=settings.langgraph_primary_snapshot_max_files,
                ),
                boundary_adapters={},
                writer_owner_id="langgraph-primary-default",
                writer_secret=(
                    settings.langgraph_primary_owner_token
                    or "default-plan-packet-binding-only"
                ),
                timeout_seconds=settings.provider_timeout_seconds,
                typed_effect_grant_kinds=typed_external_effect_grant_kinds(settings),
            )
        self._packet_router = packet_router
        self._records: dict[str, TaskRecord] = {}
        self._effect_grants: dict[str, dict | None] = {}
        self._execution_modes: dict[str, str] = {}
        self._lock = threading.RLock()

    def register(self, record: TaskRecord, *, effect_grant: dict | None = None,
                 execution_mode: str | None = None) -> None:
        with self._lock:
            self._records[record.task_id] = record.model_copy(deep=True)
            self._effect_grants[record.task_id] = None if effect_grant is None else dict(effect_grant)
            self._execution_modes[record.task_id] = execution_mode or (
                "offline_simulation" if self.settings.langgraph_primary_offline_simulation_enabled
                else "live" if self._live_composition else "paid_off")

    def discard(self, task_id: str) -> None:
        with self._lock:
            self._records.pop(task_id, None)
            self._effect_grants.pop(task_id, None)
            self._execution_modes.pop(task_id, None)

    def hydrate(self, projection: PrimaryTaskProjection) -> None:
        """Rehydrate bounded service input from the graph-owned task projection."""

        application = projection.projection.get("application")
        if not isinstance(application, Mapping):
            raise RuntimeError("primary recovery projection lacks application state")
        raw = application.get("task_record")
        if not isinstance(raw, Mapping):
            raise RuntimeError("primary recovery projection lacks its task record")
        self.register(TaskRecord.model_validate(dict(raw)), effect_grant=application.get("effect_grant"),
                      execution_mode=application.get("execution_mode", "unknown"))

    def _record(self, task_id: str) -> TaskRecord:
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                raise RuntimeError("Default primary task application state is unavailable")
            return record

    def application_projection(self, task_id: str) -> Mapping[str, Any] | None:
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return None
            return {
                "schema_version": "director-default-task-view/v1",
                "task_record": record.model_dump(mode="json"),
                "effect_grant": self._effect_grants.get(task_id),
                "execution_mode": self._execution_modes[task_id],
            }

    def authenticate_scope(self, context: HandlerContext) -> PhaseResult:
        record = self._record(context.task_id)
        return PhaseResult(
            _digest(
                {
                    "task_id": record.task_id,
                    "intake_authentication": record.intake_authentication.value,
                    "channel_id": record.intake.channel_id,
                    "user_id": record.intake.user_id,
                    "scope_digest": context.scope_digest,
                }
            )
        )

    def canonicalize(self, context: HandlerContext) -> PhaseResult:
        record = self._record(context.task_id)
        return PhaseResult(_digest(record.intake.model_dump(mode="json")))

    def plan(self, context: HandlerContext) -> PlanResult:
        record = self._record(context.task_id)
        # Intake pre-validates and freezes this deterministic plan so graph budget
        # units and the dispatched packet are derived from the same immutable input.
        if record.plan is None:
            record.plan = preview_plan(record.intake.text, self.settings)
        record.plan_generation += 1
        if not record.plan.assignments:
            raise RuntimeError("Default primary plan has no packet assignment")
        assignment = min(record.plan.assignments, key=lambda item: item.sequence)
        plan_digest = _digest(record.plan.model_dump(mode="json"))
        work_packet = validate_packet(
            "work", self._packet_router.planned_work_packet(record, assignment)
        )
        record.status = TaskStatus.OWNER_DECISION_REQUIRED
        record.message = (
            "LangGraph created a live-authorized plan and stopped at its "
            "execution approval interrupt."
            if self._live_composition
            else "LangGraph created a paid-off preview and stopped at its execution "
            "approval interrupt; no provider or external effect was called."
        )
        return PlanResult(
            plan_digest=plan_digest,
            work_packet_digest=document_digest(work_packet),
            passed=not record.workload_selection_hold_reasons,
            reason_code=(record.workload_selection_hold_reasons[0] if record.workload_selection_hold_reasons else "ok"),
        )

    def policy_budget(self, context: HandlerContext) -> PhaseResult:
        record = self._record(context.task_id)
        if record.plan is None:
            return PhaseResult(
                _digest({"task_id": context.task_id, "reason": "plan_missing"}),
                passed=False,
                reason_code="plan_missing",
            )
        return PhaseResult(
            _digest(
                {
                    "task_id": context.task_id,
                    "paid_execution": self._live_composition,
                    "estimated_costs": [
                        assignment.estimated_cost_usd
                        for assignment in record.plan.assignments
                    ],
                    "max_task_budget_usd": self.settings.max_task_budget_usd,
                }
            )
        )

    def schedule(self, context: HandlerContext) -> ScheduleResult:
        record = self._record(context.task_id)
        if record.plan is None or not record.plan.assignments:
            return ScheduleResult(
                _digest({"task_id": context.task_id, "reason": "schedule_empty"}),
                "owner",
                passed=False,
                reason_code="schedule_empty",
            )
        assignment = min(record.plan.assignments, key=lambda item: item.sequence)
        provider_lane = (
            _assignment_provider_lane(assignment)
            if self._live_composition
            else assignment.agent.value
        )
        return ScheduleResult(
            _digest(
                {
                    "task_id": context.task_id,
                    "sequence": assignment.sequence,
                    "provider": provider_lane,
                }
            ),
            provider_lane,
        )

    @staticmethod
    def _external_boundary_disabled(_: HandlerContext) -> Any:
        raise RuntimeError(
            "paid-off Default primary activation cannot cross an external boundary"
        )

    def handlers(self) -> PrimaryHandlers:
        return PrimaryHandlers(
            authenticate_scope=self.authenticate_scope,
            canonicalize=self.canonicalize,
            plan=self.plan,
            policy_budget=self.policy_budget,
            schedule=self.schedule,
            provider_subgraph=self._external_boundary_disabled,
            evidence_debate=self._external_boundary_disabled,
            openai_acceptance_revision=self._external_boundary_disabled,
            exact_sha_review=self._external_boundary_disabled,
            ci_security_completion=self._external_boundary_disabled,
            effect_execution_readback=self._external_boundary_disabled,
        )


# The charter requires this exact label, verbatim, for any test, projection or
# replay that contacted no provider (docs/team-operating-charter.md).  The
# offline dispatcher persists outcomes into the human-visible TaskRecord
# projection, so the label has to travel with them.
_SIMULATION_LABEL = "SIMULATION — NO PROVIDER INTERACTION"
_OFFLINE_RESULT_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class DefaultOfflineSimulationServices(DefaultDryRunServices):
    """Full graph services backed only by deterministic, paid-off doubles."""

    def __init__(
        self,
        settings: Settings,
        *,
        provider_doubles: Mapping[str, Callable[[HandlerContext], str]] | None = None,
        **kwargs: Any,
    ) -> None:
        if not settings.langgraph_primary_offline_simulation_enabled:
            raise ValueError("offline simulation services require their explicit switch")
        super().__init__(settings, **kwargs)
        if provider_doubles is None:
            provider_doubles = {
                provider: (
                    lambda context, selected=provider: _digest(
                        {
                            "task_id": context.task_id,
                            "provider": selected,
                            "mode": "offline-provider-double",
                        }
                    )
                )
                for provider in settings.langgraph_primary_required_providers
            }
        if set(provider_doubles) != set(settings.langgraph_primary_required_providers):
            raise ValueError("offline doubles must exactly cover required providers")
        if any(not callable(handler) for handler in provider_doubles.values()):
            raise ValueError("every offline provider double must be callable")
        self._provider_doubles = dict(provider_doubles)
        self._provider_calls = {provider: 0 for provider in provider_doubles}
        self._provider_call_lock = threading.Lock()

    @property
    def provider_call_counts(self) -> Mapping[str, int]:
        with self._provider_call_lock:
            return dict(self._provider_calls)

    def has_ledger_double(self, provider: str) -> bool:
        """Report whether a lane has a configured offline double.

        The dispatcher checks this before invoking, so an unconfigured lane
        settles as a known FAILED rather than raising. A raise here would reach
        the ledger as AMBIGUOUS, which retains the concurrency slot and blocks
        terminal lifecycle processing for a failure that is already known
        before any execution begins.
        """

        return provider in self._provider_doubles

    def dispatch_ledger_double(
        self,
        *,
        provider: str,
        context: HandlerContext,
        reservation_digest: str,
    ) -> str:
        """Invoke exactly one lane's offline double for a ledger reservation.

        The Default lifecycle dispatches through the Postgres parallel ledger
        rather than the graph's single-item provider edge, so the doubles must
        be reachable from a reservation. Without this seam an offline task
        proves intake, planning and approval but never executes a provider
        wave, because the ledger has no dispatcher to call.

        Contacts no provider and produces no external effect. The returned
        digest binds the reservation, so two lanes and two reservations of the
        same lane stay distinguishable in the ledger.
        """

        if provider not in self._provider_doubles:
            raise RuntimeError("offline ledger dispatch has no double for this lane")
        with self._provider_call_lock:
            self._provider_calls[provider] += 1
        result_digest = self._provider_doubles[provider](context)
        # Validate the whole digest, not just its prefix. A malformed value such
        # as "sha256:x" would otherwise be wrapped into a newly valid digest and
        # settle the ledger assignment as SUCCEEDED, letting the graph report
        # DONE while the double had violated its declared result contract.
        if not isinstance(result_digest, str) or _OFFLINE_RESULT_DIGEST.fullmatch(
            result_digest
        ) is None:
            raise RuntimeError("offline provider double returned an invalid digest")
        return _digest(
            {
                "reservation_digest": reservation_digest,
                "provider": provider,
                "provider_result_digest": result_digest,
                "mode": "offline-ledger-double",
            }
        )

    @property
    def provider_calls(self) -> int:
        return sum(self.provider_call_counts.values())

    def provider_subgraph(self, context: HandlerContext) -> ProviderSubgraphResult:
        if context.provider_attempt_digest is None or context.scheduled_producer_id is None:
            raise RuntimeError("offline provider attempt is not durably bound")
        if context.work_packet_digest is None:
            raise RuntimeError("offline provider packet is not post-plan bound")
        def invoke(provider: str) -> tuple[str, str]:
            with self._provider_call_lock:
                self._provider_calls[provider] += 1
            result_digest = self._provider_doubles[provider](context)
            # Validate the whole digest here too. This path settles no ledger
            # row, but a malformed value such as "sha256:x" is malformed on
            # every path that reads one: a prefix-only check folded it into the
            # subgraph result digest, so the graph carried a double's contract
            # violation forward as a COMPLETED disposition.
            if not isinstance(result_digest, str) or _OFFLINE_RESULT_DIGEST.fullmatch(
                result_digest
            ) is None:
                raise RuntimeError("offline provider double returned an invalid digest")
            return provider, result_digest

        with ThreadPoolExecutor(
            max_workers=len(self._provider_doubles),
            thread_name_prefix="director-offline-provider",
        ) as executor:
            results = dict(executor.map(invoke, sorted(self._provider_doubles)))
        return ProviderSubgraphResult(
            provider_attempt_digest=context.provider_attempt_digest,
            idempotency_key_digest=context.idempotency_key_digest,
            authority_snapshot_digest=context.authority_snapshot_digest,
            producer_id=context.scheduled_producer_id,
            disposition=ProviderDisposition.COMPLETED,
            result_digest=_digest(
                {
                    "task_id": context.task_id,
                    "provider_attempt_digest": context.provider_attempt_digest,
                    "work_packet_digest": context.work_packet_digest,
                    "provider_results": results,
                    "mode": "offline-full-team-simulation",
                }
            ),
            candidate_sha=context.starting_sha,
            reason_code="offline_provider_double_completed",
        )

    @staticmethod
    def evidence_debate(context: HandlerContext) -> EvidenceDebateResult:
        return EvidenceDebateResult(
            _digest({"task_id": context.task_id, "kind": "offline-evidence"}),
            _digest({"task_id": context.task_id, "kind": "offline-debate"}),
            context.candidate_sha,
            reason_code="offline_evidence_completed",
        )

    @staticmethod
    def openai_acceptance_revision(context: HandlerContext) -> AcceptanceResult:
        return AcceptanceResult(
            _digest({"task_id": context.task_id, "kind": "offline-acceptance"}),
            context.candidate_sha,
            AcceptanceDecision.ACCEPT,
            reason_code="offline_acceptance_completed",
        )

    @staticmethod
    def exact_sha_review(context: HandlerContext) -> ExactShaReviewResult:
        if context.producer_id is None:
            raise RuntimeError("offline exact-SHA review lacks a producer")
        reviewer = "claude" if context.producer_id == "cursor" else "cursor"
        return ExactShaReviewResult(
            _digest(
                {
                    "task_id": context.task_id,
                    "candidate_sha": context.candidate_sha,
                    "producer": context.producer_id,
                    "reviewer": reviewer,
                }
            ),
            context.candidate_sha,
            context.producer_id,
            reviewer,
            reason_code="offline_exact_sha_review_completed",
        )

    @staticmethod
    def ci_security_completion(context: HandlerContext) -> CompletionGateResult:
        return CompletionGateResult(
            _digest({"task_id": context.task_id, "gate": "offline-ci"}),
            _digest({"task_id": context.task_id, "gate": "offline-security"}),
            _digest({"task_id": context.task_id, "gate": "offline-completion"}),
            context.candidate_sha,
            reason_code="offline_completion_gates_passed",
        )

    def handlers(self) -> PrimaryHandlers:
        handlers = super().handlers()
        return PrimaryHandlers(
            authenticate_scope=handlers.authenticate_scope,
            canonicalize=handlers.canonicalize,
            plan=handlers.plan,
            policy_budget=handlers.policy_budget,
            schedule=handlers.schedule,
            provider_subgraph=self.provider_subgraph,
            evidence_debate=self.evidence_debate,
            openai_acceptance_revision=self.openai_acceptance_revision,
            exact_sha_review=self.exact_sha_review,
            ci_security_completion=self.ci_security_completion,
            effect_execution_readback=handlers.effect_execution_readback,
        )


def build_offline_parallel_dispatcher(
    services: DefaultOfflineSimulationServices,
    coordinator: Callable[[], Any],
) -> Callable[[ParallelReservation], ParallelDispatchOutcome]:
    """Build the ledger dispatcher an offline-simulation deployment needs.

    The Default lifecycle sets ``auto_execute_provider=False`` and dispatches
    exclusively through the Postgres parallel ledger. Without a dispatcher the
    ledger settles nothing, so an offline task proved intake, planning and
    approval while its provider wave never ran.

    ``coordinator`` is a late-bound accessor because the activation builds its
    coordinator after choosing a dispatcher.
    """

    def dispatch_offline_assignment(
        reservation: ParallelReservation,
    ) -> ParallelDispatchOutcome:
        """Settle one reserved offline lane through the ledger.

        Mirrors the live dispatcher's reservation-identity checks so an offline
        proof exercises the same binding rules, then substitutes the lane's
        deterministic double for the adapter, attempt roster and budget path.
        No provider is contacted and no result-packet artifact is persisted, so
        a simulated wave can never be mistaken for authenticated provider
        evidence.
        """

        if not services.has_ledger_double(reservation.provider_id):
            # Settled, not raised. This failure is known before any execution
            # begins, and an exception would reach the ledger as AMBIGUOUS,
            # retaining the concurrency slot and stopping the lifecycle from
            # ever reaching a terminal state. Identity violations below still
            # raise: those are unexpected and must not be downgraded.
            return ParallelDispatchOutcome(
                ParallelAssignmentStatus.FAILED,
                None,
                "offline_provider_double_missing",
            )
        record = services._record(reservation.task_id)
        if record.plan is None:
            raise RuntimeError("parallel dispatch has no durable plan")
        assignment = next(
            (
                item
                for item in record.plan.assignments
                if item.sequence == reservation.sequence
            ),
            None,
        )
        if assignment is None:
            raise RuntimeError("parallel reservation identity is unknown")
        assignment_id = services._packet_router._assignment_id(record, assignment)
        provider = _assignment_provider_lane(assignment)
        if (
            assignment_id != reservation.assignment_id
            or provider != reservation.provider_id
        ):
            raise RuntimeError("parallel reservation binding changed")
        result_digest = services.dispatch_ledger_double(
            provider=provider,
            context=_handler_context_from_state(
                coordinator()._runtime.compact_state(reservation.task_id)
            ),
            reservation_digest=reservation.reservation_digest,
        )
        with services._lock:
            if not any(
                item.assignment_sequence == assignment.sequence
                for item in record.assignment_outcomes
            ):
                record.assignment_outcomes.append(
                    AssignmentOutcome(
                        assignment_sequence=assignment.sequence,
                        provider=assignment.agent,
                        run_id=f"offline-double:{reservation.reservation_digest}",
                        result=_SIMULATION_LABEL,
                        review_summary=(
                            f"{_SIMULATION_LABEL} - offline provider double"
                        ),
                        provider_result_digest=result_digest.removeprefix("sha256:"),
                    )
                )
        return ParallelDispatchOutcome(
            ParallelAssignmentStatus.SUCCEEDED,
            result_digest,
            "offline_provider_double_completed",
        )

    return dispatch_offline_assignment


class HistoricalParallelPlanHold(RuntimeError):
    """An exact pre-allocation binding requires explicit reconciliation."""


def _historical_parallel_binding(payload: Mapping[str, Any]) -> str | None:
    application = payload.get("application")
    raw = application.get("task_record") if isinstance(application, Mapping) else None
    plan = raw.get("plan") if isinstance(raw, Mapping) else None
    if not isinstance(plan, Mapping) or not plan.get("assignments"):
        return None
    if any("allocation_profile" in item for item in plan["assignments"]):
        return None
    record = TaskRecord.model_validate(dict(raw))
    assignments = []
    for item in sorted(record.plan.assignments, key=lambda value: value.sequence):
        lane = _assignment_provider_lane(item)
        assignments.append({
            "assignment_id": f"{record.task_id}-{item.sequence}",
            "sequence": item.sequence,
            "provider_id": lane,
            "deployment_id": item.model_label or lane,
            "dependencies": [f"{record.task_id}-{value}" for value in item.depends_on],
            "write_surfaces": ["repository-write"] if item.allow_draft_pr else [],
            "retry_limit": 3,
        })
    # Hash the surviving raw plan: model defaults are not historical authority.
    digest = _digest({"task_id": record.task_id, "plan_digest": _digest(plan),
                      "assignments": assignments})
    return digest if digest == payload.get("parallel_plan_digest") else None


class DefaultPrimaryApplication:
    """Route Default Director intake and reads through one primary coordinator."""

    def __init__(
        self,
        settings: Settings,
        coordinator: PrimaryTaskCoordinator,
        services: DefaultDryRunServices,
        authority: DefaultPrimaryAuthority,
        parallel_dispatcher: (
            Callable[[ParallelReservation], ParallelDispatchOutcome] | None
        ) = None,
    ) -> None:
        self.settings = settings
        self.coordinator = coordinator
        self.services = services
        self.authority = authority
        # The coordinator's exact typed external-effect grant, created from
        # configuration and verified repository authority before any dispatch.
        self.effect_grant = build_typed_external_effect_grant(settings, authority)
        self._parallel_dispatcher = parallel_dispatcher

    def _parallel_policy(self) -> ParallelDispatchPolicy:
        """Return the bounded Default policy used for every durable wave."""

        provider_caps = self.settings.parallel_provider_caps()
        provider_caps["antigravity"] = provider_caps.pop("gemini")
        for provider in self.settings.langgraph_primary_required_providers:
            provider_caps.setdefault(provider, 1)
        return ParallelDispatchPolicy(
            max_concurrency=self.settings.langgraph_parallel_initial_max_concurrency,
            provider_caps=provider_caps,
        )

    def _parallel_assignments(
        self, record: TaskRecord
    ) -> tuple[ParallelAssignment, ...]:
        """Bind every plan member to its exact packet and DAG identity."""

        if record.plan is None or not record.plan.assignments:
            raise RuntimeError("primary parallel lifecycle requires a non-empty plan")
        identifiers = {
            item.sequence: self.services._packet_router._assignment_id(record, item)
            for item in record.plan.assignments
        }
        return tuple(
            ParallelAssignment(
                assignment_id=identifiers[item.sequence],
                sequence=item.sequence,
                provider_id=_assignment_provider_lane(item),
                deployment_id=item.model_label or _assignment_provider_lane(item),
                allocation_profile=item.allocation_profile,
                dependencies=tuple(identifiers[sequence] for sequence in item.depends_on),
                write_surfaces=("repository-write",) if item.allow_draft_pr else (),
                retry_limit=3,
            )
            for item in sorted(record.plan.assignments, key=lambda value: value.sequence)
        )

    def _register_parallel_lifecycle(self, record: TaskRecord) -> str:
        if record.plan is None:
            raise RuntimeError("primary parallel lifecycle requires a plan")
        return self.coordinator.register_parallel_plan(
            task_id=record.task_id,
            plan_digest=_digest(record.plan.model_dump(mode="json")),
            assignments=self._parallel_assignments(record),
            policy=self._parallel_policy(),
        )

    def _ensure_parallel_lifecycle(self, record: TaskRecord) -> str:
        """Idempotently repair a crash after graph intake but before registration."""

        self._check_historical_parallel_hold(record.task_id)
        if record.workload_selection_hold_reasons:
            return _digest(record.workload_selection_hold_reasons)
        return self._register_parallel_lifecycle(record)

    def _check_historical_parallel_hold(self, task_id: str) -> None:
        projection = self.coordinator.get_projection(task_id)
        if projection is None:
            return
        historical = _historical_parallel_binding(projection.projection)
        if historical is None:
            return
        state = self.coordinator._runtime.compact_state(task_id)
        if state.get("parallel_plan_digest") != historical:
            raise ValueError("parallel plan binding changed")
        raise HistoricalParallelPlanHold("historical_parallel_plan_requires_reconciliation")

    def _drive_parallel_lifecycle(self, record: TaskRecord) -> ParallelAggregate:
        """Consume every ready wave, stopping on terminal or no durable progress."""

        if self._parallel_dispatcher is None:
            return self.coordinator.parallel_aggregate(record.task_id)
        aggregate = self.coordinator.parallel_aggregate(record.task_id)
        plan_digest = self.coordinator.get_projection(record.task_id)
        if plan_digest is None:
            raise RuntimeError("primary parallel projection disappeared")
        bound_plan = plan_digest.projection.get("parallel_plan_digest")
        if not isinstance(bound_plan, str):
            raise RuntimeError("primary parallel plan is not durably bound")
        seen_aggregates: set[str] = set()
        while aggregate.aggregate_digest not in seen_aggregates:
            seen_aggregates.add(aggregate.aggregate_digest)
            if aggregate.terminal or aggregate.ambiguous_slot_count:
                break
            aggregate = self.coordinator.execute_parallel_ready(
                task_id=record.task_id,
                plan_digest=bound_plan,
                policy=self._parallel_policy(),
                dispatcher=self._parallel_dispatcher,
            )
        if aggregate.terminal and not aggregate.ambiguous_slot_count:
            # The ledger never classifies the task.  It supplies one immutable
            # all-assignment result to the graph, which then owns evidence,
            # review, CI/security, effects and final classification.
            self.coordinator.resume_parallel_aggregate(
                task_id=record.task_id,
                aggregate=aggregate,
                candidate_sha=self.authority.starting_sha,
            )
        return aggregate

    def _request(self, record: TaskRecord) -> PrimaryRequest:
        task_id = record.task_id
        intake = record.intake
        if record.plan is None or not record.plan.assignments:
            raise RuntimeError("primary intake requires a validated non-empty plan")
        provider_units = sum(
            max(1, assignment.effort_points) for assignment in record.plan.assignments
        )
        effect_units = sum(
            1 for assignment in record.plan.assignments if assignment.allow_draft_pr
        )
        task_units = provider_units + effect_units
        intake_payload = intake.model_dump(mode="json")
        intake_digest = _digest(intake_payload)
        scope_digest = _digest(
            {
                "app_env": self.settings.app_env,
                "projects": self.settings.project_allowlist,
                "repositories": self.settings.repository_allowlist,
                "starting_refs": self.settings.allowed_starting_refs,
            }
        )
        idempotency_key_digest = _digest(
            {
                "source_event_id": intake.source_event_id,
                "channel_id": intake.channel_id,
                "thread_ts": intake.thread_ts,
            }
        )
        dedup_payload_digest = _digest(
            {
                "source_event_id": intake.source_event_id,
                "channel_id": intake.channel_id,
                "thread_ts": intake.thread_ts,
                "user_id": intake.user_id,
                "text": intake.text,
                "permalink": intake.permalink,
            }
        )
        return PrimaryRequest(
            task_id=task_id,
            source_event_id="source-" + idempotency_key_digest.removeprefix("sha256:")[:24],
            correlation_id="correlation-" + secrets.token_hex(12),
            intake_digest=intake_digest,
            scope_digest=scope_digest,
            authority_snapshot_digest=self.authority.authority_snapshot_digest,
            # Compatibility input only. The graph quarantines it until plan()
            # returns the digest of a validated model-work-packet-v1 document.
            work_packet_digest=_digest(
                {
                    "intake_digest": intake_digest,
                    "scope_digest": scope_digest,
                    "authority_snapshot_digest": (
                        self.authority.authority_snapshot_digest
                    ),
                    "starting_sha": self.authority.starting_sha,
                }
            ),
            idempotency_key_digest=idempotency_key_digest,
            starting_sha=self.authority.starting_sha,
            dedup_payload_digest=dedup_payload_digest,
            writer_owner_id="langgraph",
            budget_limit_units=task_units,
            provider_cost_units=provider_units,
            effect_cost_units=effect_units,
            execution_approval_required=True,
            external_effect_required=effect_units > 0,
            effect_key_digest=(
                _digest(
                    {
                        "task_id": task_id,
                        "plan": record.plan.model_dump(mode="json"),
                        "effect": "authorized_assignment_effect",
                        # Owner approval digests the exact typed grant: kind,
                        # repository, configured pull request, base and head. A
                        # different grant is a different approval identity.
                        "effect_grant": (
                            None
                            if self.effect_grant is None
                            else self.effect_grant.as_dict()
                        ),
                    }
                )
                if effect_units > 0
                else None
            ),
            graph_writer_active=True,
            legacy_writer_active=False,
        )

    @staticmethod
    def _record_from_projection(projection: PrimaryTaskProjection) -> TaskRecord:
        application = projection.projection.get("application")
        if not isinstance(application, Mapping):
            raise RuntimeError("primary task projection lacks application state")
        raw = application.get("task_record")
        if not isinstance(raw, Mapping):
            raise RuntimeError("primary task projection lacks its task record")
        record = TaskRecord.model_validate(dict(raw))
        classification = projection.projection.get("terminal_classification")
        if classification == "SUCCEEDED":
            record.status = TaskStatus.DONE
        elif classification == "OWNER_DECISION_REQUIRED":
            record.status = TaskStatus.OWNER_DECISION_REQUIRED
        elif classification in {"RECONCILIATION_REQUIRED", "POLICY_BLOCKED"}:
            record.status = TaskStatus.BLOCKED
        elif classification is not None:
            record.status = TaskStatus.FAILED
        elif projection.projection.get("run_status") == "RUNNING":
            record.status = TaskStatus.RUNNING
        return record

    def create_intake(
        self,
        intake: SlackIntake,
        *,
        intake_authentication: IntakeAuthentication,
        authenticated_replay_identity: tuple[str, str] | None = None,
    ) -> TaskRecord:
        task_id = "DIR-LG-" + secrets.token_hex(5).upper()
        record = TaskRecord(
            task_id=task_id,
            status=TaskStatus.QUEUED,
            intake=intake,
            intake_authentication=intake_authentication,
        )
        record.plan = preview_plan(record.intake.text, self.settings)
        if self.services._live_composition:
            from .primary_workload import read_admission_history, WorkloadAdmissionHold
            from .primary_roster import PrimaryRosterHold
            try:
                with self.coordinator._store._pool.connection() as connection:
                    history = read_admission_history(connection, schema=self.coordinator._store._schema)
                _live_parallel_plan(record, self.settings,
                    available=tuple(p for p in self.services.available_provider_lanes if self.services.provider_lane_ready(p)), history=history)
            except (PrimaryRosterHold, WorkloadAdmissionHold) as hold:
                record.workload_selection_hold_reasons = ["primary_roster_" + str(hold)]

        request = self._request(record)
        if authenticated_replay_identity is not None:
            claimed_task_id, created = self.coordinator.claim_authenticated_intake(
                nonce_digest="sha256:" + authenticated_replay_identity[0],
                idempotency_key_digest=request.idempotency_key_digest,
                payload_digest="sha256:" + authenticated_replay_identity[1],
                task_id=task_id,
            )
            task_id = claimed_task_id
            if not created:
                existing = self.coordinator.get_projection(task_id)
                if existing is not None:
                    replayed = self._record_from_projection(existing)
                    self._ensure_parallel_lifecycle(replayed)
                    repaired = self.coordinator.get_projection(task_id)
                    if repaired is None:
                        raise RuntimeError("replayed primary intake disappeared")
                    return self._record_from_projection(repaired)
            record.task_id = task_id
            request = self._request(record)
        self.services.register(record, effect_grant=(
            None if self.effect_grant is None else self.effect_grant.as_dict()))
        try:
            projection = self.coordinator.create_intake(request)
            planned = self._record_from_projection(projection)
            self._ensure_parallel_lifecycle(planned)
            refreshed = self.coordinator.get_projection(planned.task_id)
            if refreshed is None:
                raise RuntimeError("primary parallel registration lost its projection")
            return self._record_from_projection(refreshed)
        finally:
            self.services.discard(task_id)

    def get_task(self, task_id: str) -> TaskRecord | None:
        projection = self.coordinator.get_projection(task_id)
        return (
            None
            if projection is None
            else self._record_from_projection(projection)
        )

    def resume_approval(self, command: ApprovalResume) -> TaskRecord:
        self._check_historical_parallel_hold(command.task_id)
        projection = self.coordinator.get_projection(command.task_id)
        if projection is None:
            raise KeyError(command.task_id)
        if (
            command.decision == ApprovalDecision.APPROVE
            and not self.settings.langgraph_primary_offline_simulation_enabled
            and not self.settings.langgraph_primary_live_provider_dispatch_enabled
            and self._parallel_dispatcher is None
        ):
            raise RuntimeError(
                "paid-off Default primary activation cannot approve provider execution"
            )
        self.services.hydrate(projection)
        try:
            updated = self.coordinator.resume_approval(command)
            record = self._record_from_projection(updated)
            if command.decision == ApprovalDecision.APPROVE:
                self._drive_parallel_lifecycle(record)
                refreshed = self.coordinator.get_projection(command.task_id)
                if refreshed is None:
                    raise RuntimeError("primary parallel approval lost its projection")
                updated = refreshed
        finally:
            self.services.discard(command.task_id)
        return self._record_from_projection(updated)

    def cancel(self, task_id: str, *, expected_fencing_generation: int | None = None) -> TaskRecord:
        projection = self.coordinator.get_projection(task_id)
        if projection is None:
            raise KeyError(task_id)
        self.services.hydrate(projection)
        try:
            updated = self.coordinator.cancel(task_id, **(
                {"expected_fencing_generation": expected_fencing_generation}
                if expected_fencing_generation is not None else {}
            ))
        finally:
            self.services.discard(task_id)
        return self._record_from_projection(updated)

    def record_provider_result(
        self, task_id: str, command: ProviderSubgraphResult
    ) -> TaskRecord:
        projection = self.coordinator.get_projection(task_id)
        if projection is None:
            raise KeyError(task_id)
        self.services.hydrate(projection)
        try:
            updated = self.coordinator.record_provider_result(task_id, command)
        finally:
            self.services.discard(task_id)
        return self._record_from_projection(updated)

    def reconcile_effect(
        self, task_id: str, command: EffectExecutionResult
    ) -> TaskRecord:
        projection = self.coordinator.get_projection(task_id)
        if projection is None:
            raise KeyError(task_id)
        self.services.hydrate(projection)
        try:
            updated = self.coordinator.reconcile_effect(task_id, command)
        finally:
            self.services.discard(task_id)
        return self._record_from_projection(updated)

    def record_provider_callback(
        self,
        *,
        task_id: str,
        assignment_id: str,
        provider_id: str,
        provider_run_id: str,
        command: ProviderSubgraphResult,
    ) -> TaskRecord:
        """Settle a provider callback in the parallel ledger, never the old edge."""

        if not provider_run_id.strip():
            raise ValueError("provider callback run identity is required")
        projection = self.coordinator.get_projection(task_id)
        if projection is None:
            raise KeyError(task_id)
        status = {
            ProviderDisposition.COMPLETED: ParallelAssignmentStatus.SUCCEEDED,
            ProviderDisposition.FAILED: ParallelAssignmentStatus.FAILED,
            ProviderDisposition.AMBIGUOUS: ParallelAssignmentStatus.AMBIGUOUS,
        }[command.disposition]
        self.services.hydrate(projection)
        try:
            aggregate = self.coordinator.record_parallel_callback(
                task_id=task_id,
                assignment_id=assignment_id,
                provider_id=provider_id,
                attempt_digest=command.provider_attempt_digest,
                provider_run_id_digest=_digest(
                    {"provider_run_id": provider_run_id}
                ),
                status=status,
                result_digest=command.result_digest,
                reason_code=command.reason_code,
            )
            record = self._record_from_projection(
                self.coordinator.get_projection(task_id) or projection
            )
            if aggregate.terminal and not aggregate.ambiguous_slot_count:
                self.coordinator.resume_parallel_aggregate(
                    task_id=task_id,
                    aggregate=aggregate,
                    candidate_sha=(command.candidate_sha or self.authority.starting_sha),
                )
            refreshed = self.coordinator.get_projection(task_id)
            if refreshed is None:
                raise RuntimeError("provider callback lost its projection")
            return self._record_from_projection(refreshed)
        finally:
            self.services.discard(task_id)

    def prepare_external_effect(self, task_id: str) -> EffectReservation:
        projection = self.coordinator.get_projection(task_id)
        if projection is None:
            raise KeyError(task_id)
        self.services.hydrate(projection)
        try:
            return self.coordinator.prepare_external_effect(task_id)
        finally:
            self.services.discard(task_id)

    def bind_external_effect_remote(
        self,
        *,
        reservation: EffectReservation,
        remote_system: str,
        remote_identifier: str,
    ) -> None:
        self.coordinator.bind_external_effect_remote(
            reservation=reservation,
            remote_system=remote_system,
            remote_identifier=remote_identifier,
        )

    def read_external_effect_remote(
        self, reservation: EffectReservation
    ) -> Mapping[str, str] | None:
        return self.coordinator.read_external_effect_remote(reservation)

    def recover(self, task_id: str) -> TaskRecord:
        self._check_historical_parallel_hold(task_id)
        projection = self.coordinator.get_projection(task_id)
        if projection is None:
            raise KeyError(task_id)
        self.services.hydrate(projection)
        try:
            self._ensure_parallel_lifecycle(self._record_from_projection(projection))
            updated = self.coordinator.recover(task_id)
            record = self._record_from_projection(updated)
            if updated.projection.get("provider_dispatch_status") != "NOT_STARTED":
                self._drive_parallel_lifecycle(record)
                refreshed = self.coordinator.get_projection(task_id)
                if refreshed is None:
                    raise RuntimeError("primary parallel recovery lost its projection")
                updated = refreshed
        finally:
            self.services.discard(task_id)
        return self._record_from_projection(updated)


class DefaultPrimaryActivation:
    """Own the Default profile's Postgres handle, lease, and application router."""

    def __init__(
        self,
        settings: Settings,
        *,
        live_dependencies: DefaultPrimaryLiveDependencies | None = None,
        team_routing=None,
        effect_mutation: EffectMutation | None = None,
        effect_readback: EffectReadback | None = None,
        effect_gateways: ExactEffectGatewayRegistry | None = None,
    ) -> None:
        self.settings = settings
        self.handle: PostgresCheckpointHandle | None = None
        self.coordinator: PrimaryTaskCoordinator | None = None
        self.application: DefaultPrimaryApplication | None = None
        self.services: DefaultDryRunServices | None = None
        self.provider_activation: PrimaryProviderActivationService | None = None
        self._reconciliation_worker_ready = False
        self._live_dependencies_ready = False
        if (effect_mutation is None) != (effect_readback is None):
            raise ValueError(
                "external effect mutation and readback must be configured together"
            )
        if not settings.langgraph_primary_enabled:
            return
        live_requested = settings.langgraph_primary_live_provider_dispatch_enabled
        if settings.allow_paid_runs and not live_requested:
            raise ValueError(
                "ALLOW_PAID_RUNS is enabled but "
                "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED is disabled"
            )
        authority = resolve_default_primary_authority(settings)
        effect_grant = build_typed_external_effect_grant(settings, authority)
        from .completion_proof import CompletionAuthorityVerifier

        completion_secrets = (
            settings.langgraph_primary_independent_review_evidence_secret,
            settings.langgraph_primary_ci_evidence_secret,
            settings.langgraph_primary_security_evidence_secret,
        )
        completion_verifier = (
            CompletionAuthorityVerifier(
                independent_review_secret=completion_secrets[0],
                ci_secret=completion_secrets[1],
                security_secret=completion_secrets[2],
                shared_secret=settings.shared_secret,
            )
            if any(completion_secrets)
            else None
        )
        from .acceptance_journal import require_runtime_acceptance, bind_runtime_database
        accepted_identity = require_runtime_acceptance(settings)
        handle = open_postgres_control_plane(
            settings.langgraph_primary_postgres_dsn,
            schema=settings.langgraph_primary_postgres_schema,
            allow_insecure_local_transport=(
                settings.langgraph_primary_allow_insecure_local_postgres
            ),
            completion_authority_verifier=completion_verifier,
            **({
                "accepted_storage_identity": accepted_identity.database_identity,
                "accepted_instance_id": accepted_identity.database_instance_id,
            } if accepted_identity is not None else {}),
        )
        try:
            bind_runtime_database(accepted_identity, handle)
            bindings = settings.primary_provider_activation_bindings()
            effect_executor = (
                DeterministicExternalEffectExecutor(
                    store=handle.claim_store,
                    owner_token=settings.langgraph_primary_owner_token,
                    mutation=effect_mutation,
                    readback=effect_readback,
                    timeout_seconds=min(
                        60.0, float(settings.provider_timeout_seconds)
                    ),
                )
                if effect_mutation is not None and effect_readback is not None
                else None
            )
            from .provider_validation import capture_verifier_from_environment
            provider_activation = (
                PrimaryProviderActivationService(
                    store=handle.claim_store,
                    bindings=bindings,
                    required_providers=settings.langgraph_primary_required_providers,
                    capture_verifier=capture_verifier_from_environment(
                        candidate=settings.langgraph_primary_pinned_commit_sha,
                        tree=settings.langgraph_primary_pinned_tree_sha,
                    ),
                )
                if bindings
                else None
            )
            if live_requested:
                if provider_activation is None:
                    raise ValueError(
                        "live LangGraph primary startup requires exact provider "
                        "activation bindings"
                    )
                services = DefaultDryRunServices(settings, live_composition=True)
                if live_dependencies is None:
                    live_dependencies = build_automatic_primary_live_dependencies(
                        settings, handle, services, team_routing=team_routing
                    )
                if settings.langgraph_primary_antigravity_team_enabled:
                    from .antigravity_team_routing import PrimaryTeamRouting
                    if not isinstance(getattr(live_dependencies,"team_routing",None),PrimaryTeamRouting):
                        raise ValueError("primary team mode lacks typed enrolled routing services")
                elif getattr(live_dependencies,"team_routing",None) is not None:
                    raise ValueError("team routing cannot bypass the default-off setting")
                live_dependencies.validate(settings.langgraph_primary_required_providers)
                if (live_dependencies.budget_routing.pool is not handle.pool
                        or live_dependencies.budget_routing.authority.schema != handle.schema):
                    raise ValueError("daily provider budget routing must use the active primary ledger")
                if (live_dependencies.team_routing is not None
                        and (live_dependencies.team_routing.pool is not handle.pool
                             or live_dependencies.team_routing.authority.schema != handle.schema)):
                    raise ValueError("team routing must use the active primary ledger")
                services.available_provider_lanes = tuple(live_dependencies.adapters)
                services.provider_lane_ready = lambda provider: provider_activation.provider_ready(provider, live=True)
                if effect_executor is None:
                    registry = (
                        effect_gateways
                        if effect_gateways is not None
                        else build_configured_effect_gateway_registry(settings)
                    )
                    intent_store = PostgresTypedEffectIntentStore(
                        pool=handle.pool,
                        schema=settings.langgraph_primary_postgres_schema,
                    )

                    def typed_effect_intent(
                        context: HandlerContext, reservation: EffectReservation
                    ) -> TypedEffectIntent:
                        binding = live_dependencies.packet_result_resolver(context)
                        packet = None if binding is None else binding.outcome.result_packet
                        effects = [] if packet is None else packet.get("side_effects", [])
                        proposed = [
                            item for item in effects
                            if isinstance(item, Mapping)
                            and item.get("status") == "NOT_ATTEMPTED"
                        ]
                        if len(proposed) != 1:
                            raise RuntimeError(
                                "external effect requires exactly one authenticated "
                                "NOT_ATTEMPTED side-effect proposal"
                            )
                        item = proposed[0]
                        kind = str(item.get("kind", ""))
                        if binding is None:
                            raise RuntimeError(
                                "external effect lacks its authenticated work packet"
                            )
                        system, action, target, payload = _typed_effect_binding(
                            grant=effect_grant,
                            record=services._record(context.task_id),
                            context=context,
                            granted_kinds=frozenset(
                                str(value)
                                for value in binding.work_packet.get(
                                    "allowed_external_effects", []
                                )
                            ),
                            effect=item,
                        )
                        approval = intent_store.approval_binding(
                            task_id=context.task_id,
                            effect_key_digest=reservation.effect_key_digest,
                        )
                        # The exact kind and target are bound into the durable
                        # intent identity together with the signed owner
                        # approval, so a redirected repository, pull request,
                        # base or head cannot reuse this approved reservation.
                        stable_key = _digest(
                            {
                                "provider_idempotency_key": item.get("idempotency_key"),
                                "effect_key_digest": reservation.effect_key_digest,
                                "system": system,
                                "action": action,
                                "kind": kind,
                                "target": dict(target),
                                "effect_grant": (
                                    None if effect_grant is None else effect_grant.as_dict()
                                ),
                                "approval_receipt_digest": str(
                                    approval["approval_receipt_digest"]
                                ),
                                "authorization_nonce_digest": str(
                                    approval["authorization_nonce_digest"]
                                ),
                            }
                        )
                        return TypedEffectIntent(
                            system=system,
                            action=action,
                            target=target,
                            payload=payload,
                            idempotency_key="director:" + stable_key.removeprefix("sha256:"),
                            authority_binding_digest=str(
                                approval["authorization_nonce_digest"]
                            ),
                            approval_binding_digest=str(
                                approval["approval_receipt_digest"]
                            ),
                            writer_owner_id=reservation.writer_owner_id,
                            writer_fencing_generation=reservation.fencing_generation,
                            budget_units=reservation.reserved_units,
                        )

                    effect_executor = DeterministicExternalEffectExecutor(
                        store=handle.claim_store,
                        owner_token=settings.langgraph_primary_owner_token,
                        timeout_seconds=min(
                            60.0, float(settings.provider_timeout_seconds)
                        ),
                        intent_factory=typed_effect_intent,
                        intent_store=intent_store,
                        gateways=registry,
                    )
                if not effect_executor.ready:
                    raise ValueError(
                        "live LangGraph primary requires exact typed Slack/GitHub "
                        "effect transports"
                    )
                from .primary_roster import core_inventory_ready
                if not core_inventory_ready(tuple(p for p in services.available_provider_lanes if services.provider_lane_ready(p))):
                    raise ValueError(
                        "live LangGraph primary startup requires current LIVE_OK "
                        "evidence for the mandatory core provider capabilities"
                    )
                def write_work_packet(
                    context: HandlerContext,
                    packet: Mapping[str, Any],
                    packet_digest: str,
                ) -> str:
                    writer = handle.claim_store.current_task_writer(context.task_id)
                    if (
                        writer is None
                        or writer.engine_owner != "langgraph"
                        or writer.owner_token != settings.langgraph_primary_owner_token
                    ):
                        raise RuntimeError(
                            "live work-packet persistence lacks current writer authority"
                        )
                    artifact = handle.claim_store.record_work_packet_artifact(
                        task_id=context.task_id,
                        assignment_id=str(packet["assignment_id"]),
                        packet=packet,
                        owner_token=writer.owner_token,
                        fencing_generation=writer.fencing_generation,
                    )
                    if artifact.document_digest != packet_digest:
                        raise RuntimeError(
                            "durable work-packet digest does not match planned packet"
                        )
                    return artifact.document_digest

                def resolve_work_packet(
                    context: HandlerContext,
                ) -> PrimaryProviderBinding:
                    record = services._record(context.task_id)
                    if record.plan is None or not record.plan.assignments:
                        raise RuntimeError("live provider binding has no assignment")
                    assignment = min(
                        record.plan.assignments, key=lambda item: item.sequence
                    )
                    assignment_id = services._packet_router._assignment_id(
                        record, assignment
                    )
                    artifact = handle.claim_store.read_work_packet_artifact(
                        task_id=context.task_id, assignment_id=assignment_id
                    )
                    if artifact is None:
                        raise RuntimeError("durable work-packet artifact is unavailable")
                    if context.provider_attempt_digest is None:
                        raise RuntimeError("provider attempt is not bound")
                    producer = context.scheduled_producer_id
                    if producer is None or producer != _assignment_provider_lane(
                        assignment
                    ):
                        raise RuntimeError("scheduled provider binding changed")
                    return PrimaryProviderBinding(
                        provider=producer,
                        producer_id=producer,
                        provider_attempt_digest=context.provider_attempt_digest,
                        writer_token_digest=(
                            services._packet_router.writer_token(record).digest
                        ),
                        fencing_generation=artifact.writer_fencing_generation,
                        work_packet=artifact.packet,
                    )

                def revalidate_writer_fence(
                    context: HandlerContext, binding: PrimaryProviderBinding
                ) -> int:
                    writer = handle.claim_store.current_task_writer(context.task_id)
                    if (
                        writer is None
                        or writer.engine_owner != "langgraph"
                        or writer.owner_token != settings.langgraph_primary_owner_token
                        or writer.fencing_generation != binding.fencing_generation
                    ):
                        raise RuntimeError("live provider I/O has a stale writer fence")
                    return writer.fencing_generation

                def write_result_packet(
                    context: HandlerContext,
                    binding: PrimaryProviderBinding,
                    packet: Mapping[str, Any],
                    packet_digest: str,
                ) -> str:
                    artifact = handle.claim_store.record_result_packet_artifact(
                        task_id=context.task_id,
                        assignment_id=str(packet["assignment_id"]),
                        packet=packet,
                        owner_token=settings.langgraph_primary_owner_token,
                        fencing_generation=binding.fencing_generation,
                    )
                    if artifact.document_digest != packet_digest:
                        raise RuntimeError(
                            "durable result-packet digest does not match provider result"
                        )
                    return artifact.document_digest

                from .primary_live import DefaultLiveServices

                live_services = DefaultLiveServices(
                    planning_services=services,
                    adapters=live_dependencies.adapters,
                    work_packet_artifact_writer=write_work_packet,
                    work_packet_binding_resolver=resolve_work_packet,
                    candidate_sha_resolver=live_dependencies.candidate_sha_resolver,
                    live_provider_activation=lambda provider: (
                        provider_activation.provider_ready(provider, live=True)
                    ),
                    writer_fence_resolver=revalidate_writer_fence,
                    result_artifact_writer=write_result_packet,
                    packet_result_resolver=live_dependencies.packet_result_resolver,
                    completion_evidence_resolver=(
                        live_dependencies.completion_evidence_resolver
                    ),
                    external_effect_resolver=(
                        live_dependencies.external_effect_resolver
                    ),
                    evidence_debate_handler=live_dependencies.evidence_debate_handler,
                    acceptance_handler=live_dependencies.acceptance_handler,
                )
                handlers = live_services.handlers()
                def dispatch_parallel_assignment(
                    reservation: ParallelReservation,
                ) -> ParallelDispatchOutcome:
                    record = services._record(reservation.task_id)
                    if record.plan is None:
                        raise RuntimeError("parallel dispatch has no durable plan")
                    assignment = next(
                        (
                            item
                            for item in record.plan.assignments
                            if item.sequence == reservation.sequence
                        ),
                        None,
                    )
                    if assignment is None:
                        raise RuntimeError("parallel reservation identity is unknown")
                    assignment_id = services._packet_router._assignment_id(
                        record, assignment
                    )
                    provider = _assignment_provider_lane(assignment)
                    if (
                        assignment_id != reservation.assignment_id
                        or provider != reservation.provider_id
                    ):
                        raise RuntimeError("parallel reservation binding changed")
                    if not provider_activation.provider_ready(provider, live=True):
                        return ParallelDispatchOutcome(
                            ParallelAssignmentStatus.FAILED,
                            None,
                            "provider_activation_not_ready",
                        )
                    packet = validate_packet(
                        "work",
                        services._packet_router.planned_work_packet(record, assignment),
                    )
                    packet_digest = document_digest(packet)
                    # A live lane needs a trusted positive per-lane and
                    # per-attempt limit bounded by the configured task budget
                    # before any attempt reservation or paid provider call.
                    limits = packet["limits"]
                    assignment_budget_usd = float(limits["budget_usd"])
                    attempt_budget_usd = assignment_budget_usd / 3.0
                    if (
                        assignment_budget_usd <= 0.0
                        or attempt_budget_usd <= 0.0
                        or assignment_budget_usd
                        > float(settings.max_task_budget_usd)
                    ):
                        return ParallelDispatchOutcome(
                            ParallelAssignmentStatus.FAILED,
                            None,
                            "live_lane_budget_unauthorized",
                        )
                    writer = handle.claim_store.current_task_writer(record.task_id)
                    if (
                        writer is None
                        or writer.engine_owner != "langgraph"
                        or writer.owner_token != settings.langgraph_primary_owner_token
                        or not writer.lease_live
                    ):
                        raise RuntimeError("parallel provider I/O lacks writer authority")
                    artifact = handle.claim_store.record_work_packet_artifact(
                        task_id=record.task_id,
                        assignment_id=assignment_id,
                        packet=packet,
                        owner_token=writer.owner_token,
                        fencing_generation=writer.fencing_generation,
                    )
                    if artifact.document_digest != packet_digest:
                        raise RuntimeError("parallel work-packet persistence changed")
                    format_roster = live_dependencies.format_roster(provider)
                    team_routing=getattr(live_dependencies,"team_routing",None)
                    if team_routing is not None and provider=="antigravity":
                        from .antigravity_team_routing import TEAM_FORMAT
                        format_roster=(TEAM_FORMAT,)
                    attempt_timeout_seconds = max(
                        1, int(limits["timeout_seconds"]) // 3
                    )
                    budget_evidence_digest = _digest(
                        {
                            "assignment_budget_usd": assignment_budget_usd,
                            "assignment_timeout_seconds": int(limits["timeout_seconds"]),
                            "attempt_budget_usd": attempt_budget_usd,
                            "attempt_timeout_seconds": attempt_timeout_seconds,
                            "attempt_limit": 3,
                        }
                    )
                    def reserve_attempt(
                        attempt_ordinal: int,
                    ) -> ProviderAttemptReservation:
                        idempotency_key_digest = _digest(
                            {
                                "assignment_idempotency_key": str(
                                    packet["idempotency_key"]
                                ),
                                "attempt_ordinal": attempt_ordinal,
                                "format_roster": format_roster,
                            }
                        )
                        return self.coordinator.reserve_provider_attempt(
                            reservation=reservation,
                            attempt_ordinal=attempt_ordinal,
                            format_roster=format_roster,
                            idempotency_key_digest=idempotency_key_digest,
                            budget_evidence_digest=budget_evidence_digest,
                            **live_provider_admissions(settings=settings,
                                budget_routing=live_dependencies.budget_routing,
                                team_routing=team_routing, provider=provider, packet=packet,
                                attempt_usd=attempt_budget_usd),
                        )
                    def record_attempt(
                        attempt: ProviderAttemptReservation,
                        status: ParallelAssignmentStatus,
                        result_digest: str | None,
                        reason_code: str,
                    ) -> None:
                        self.coordinator.record_provider_attempt(
                            attempt=attempt,
                            status=status,
                            result_digest=result_digest,
                            reason_code=reason_code,
                        )

                    def dispatch_attempt(
                        attempt: ProviderAttemptReservation,
                    ) -> Mapping[str, Any]:
                        adapter = (team_routing.adapter(packet,attempt)
                            if team_routing is not None and provider=="antigravity"
                            else live_dependencies.adapter_for_format(provider,(attempt.format_id,attempt.transport_id)))
                        return adapter.dispatch(
                            packet,
                            context=ProviderAdapterContext(
                                task_id=record.task_id,
                                assignment_id=assignment_id,
                                idempotency_key=attempt.attempt_digest,
                                work_packet_digest=packet_digest,
                                writer_token_digest=(
                                    services._packet_router.writer_token(record).digest
                                ),
                                timeout_seconds=attempt_timeout_seconds,
                                budget_usd=attempt_budget_usd,
                                total_token_limit=attempt.total_token_limit,
                                max_hops=int(limits["max_hops"]),
                                max_revisions=int(limits["max_revisions"]),
                                allowed_tools=tuple(packet["allowed_tools"]),
                                allowed_external_effects=tuple(
                                    packet["allowed_external_effects"]
                                ),
                                fencing_generation=writer.fencing_generation,
                            ),
                        )

                    attempt_status, result, result_digest, attempt_reason = (
                        _execute_three_attempt_roster(
                            format_roster=format_roster,
                            reserve_attempt=reserve_attempt,
                            record_attempt=record_attempt,
                            dispatch_attempt=dispatch_attempt,
                        )
                    )
                    if attempt_status is not ParallelAssignmentStatus.SUCCEEDED:
                        return ParallelDispatchOutcome(
                            attempt_status,
                            result_digest,
                            attempt_reason,
                        )
                    if result is None or result_digest is None:
                        raise RuntimeError("successful attempt lacks result evidence")
                    persisted = handle.claim_store.record_result_packet_artifact(
                        task_id=record.task_id,
                        assignment_id=assignment_id,
                        packet=result,
                        owner_token=writer.owner_token,
                        fencing_generation=writer.fencing_generation,
                    )
                    if persisted.document_digest != result_digest:
                        raise RuntimeError("parallel result-packet persistence changed")
                    with services._lock:
                        if not any(
                            item.assignment_sequence == assignment.sequence
                            for item in record.assignment_outcomes
                        ):
                            record.assignment_outcomes.append(
                                AssignmentOutcome(
                                    assignment_sequence=assignment.sequence,
                                    provider=assignment.agent,
                                    run_id=str(result["run_reference"]),
                                    result=str(result["outcome"]),
                                    review_summary=str(result["outcome"]),
                                    provider_result_digest=(
                                        result_digest.removeprefix("sha256:")
                                    ),
                                )
                            )
                    return ParallelDispatchOutcome(
                        ParallelAssignmentStatus.SUCCEEDED,
                        result_digest,
                        "provider_result_authenticated",
                    )

                parallel_dispatcher = dispatch_parallel_assignment
                self._live_dependencies_ready = True
            else:
                services = (
                    DefaultOfflineSimulationServices(settings)
                    if settings.langgraph_primary_offline_simulation_enabled
                    else DefaultDryRunServices(settings)
                )
                handlers = services.handlers()
                parallel_dispatcher = (
                    build_offline_parallel_dispatcher(
                        services, lambda: self.coordinator
                    )
                    if settings.langgraph_primary_offline_simulation_enabled
                    else None
                )
            coordinator = PrimaryTaskCoordinator(
                handle,
                handlers,
                owner_token=settings.langgraph_primary_owner_token,
                checkpoint_namespace=(
                    settings.langgraph_primary_checkpoint_namespace
                ),
                # The Default lifecycle dispatches exclusively through the
                # Postgres parallel ledger. The graph's historical single-item
                # provider edge remains an approval interrupt, never a second
                # transport owner.
                auto_execute_provider=False,
                effect_executor=effect_executor,
                application_projection_provider=(
                    services.application_projection
                ),
            )
        except Exception:
            handle.close()
            raise
        self.handle = handle
        self.coordinator = coordinator
        self.services = services
        self.provider_activation = provider_activation
        self.application = DefaultPrimaryApplication(
            settings,
            coordinator,
            services,
            authority,
            parallel_dispatcher=parallel_dispatcher,
        )

    @property
    def enabled(self) -> bool:
        return self.application is not None

    def start(self) -> None:
        if not self.enabled:
            return
        if not self.settings.langgraph_primary_new_intake_enabled:
            return
        assert self.coordinator is not None
        self.coordinator.adopt_or_activate_new_intake()

    def renew(self) -> None:
        if not self.enabled:
            return
        assert self.coordinator is not None
        self.coordinator.heartbeat_active_task_writers()
        if self.settings.langgraph_primary_new_intake_enabled:
            self.coordinator.renew_new_intake()

    def reconcile_once(self) -> int:
        if not self.enabled:
            return 0
        assert self.coordinator is not None
        assert self.services is not None
        assert self.application is not None
        # Readiness describes this bounded pass, never a sticky historical win.
        self._reconciliation_worker_ready = False
        self._historical_parallel_hold_count = 0
        self._cancelled_plan_binding_hold_count = 0
        recovered = []
        for projection in self.coordinator.list_projections():
            payload = projection.projection
            if payload.get("terminal_classification") == "CANCELLED":
                # Reconcile ledger-only cancellation residue, never reconstruct
                # or drive a terminal task's historical graph/provider plan.
                try:
                    self.coordinator.reconcile_cancelled_pending(projection.task_id)
                except CancelledPlanBindingHold:
                    # The ledger committed a specific refusal; keep readiness
                    # false while allowing diagnostics and unrelated tasks up.
                    self._cancelled_plan_binding_hold_count += 1
                    # Health is not a dispatch guard. Tolerate this refusal only in
                    # explicitly paid-off, non-live, intake-off diagnostic operation: a
                    # process that owns new intake must not start over this ledger.
                    if (self.settings.allow_paid_runs
                            or self.settings.langgraph_primary_live_provider_dispatch_enabled
                            or self.settings.langgraph_primary_new_intake_enabled):
                        raise
                continue
            if payload.get("terminal_classification") is not None:
                continue
            if _historical_parallel_binding(payload) is not None:
                try:
                    self.application._check_historical_parallel_hold(projection.task_id)
                except HistoricalParallelPlanHold:
                    self._historical_parallel_hold_count += 1
                    continue
            writer = self.coordinator.get_task_writer(projection.task_id)
            # A live durable writer lease is a positive heartbeat: its
            # DISPATCHING/EXECUTING work is normal in-flight work and must not
            # be converted to ambiguous reconciliation by a periodic sweep.
            if writer is not None and writer.lease_live:
                try:
                    aggregate = self.coordinator.parallel_aggregate(projection.task_id)
                except Exception:
                    aggregate = None
                if aggregate is not None and any(
                    value in {"ACTIVE", "AMBIGUOUS"}
                    for value in aggregate.statuses.values()
                ):
                    continue
            try:
                recovered.append(self.application.recover(projection.task_id))
            except HistoricalParallelPlanHold:
                self._historical_parallel_hold_count += 1
        self._reconciliation_worker_ready = (
            self._historical_parallel_hold_count == 0 and self._cancelled_plan_binding_hold_count == 0
        )
        return len(recovered)

    def health(self) -> dict[str, Any]:
        configured = self.settings.langgraph_primary_configuration_readiness()
        provider_activation = (
            self.provider_activation.health()
            if self.provider_activation is not None
            else {
                "app_dry_run_ready": False,
                "app_live_ready": False,
                "runtime_adapter_dry_run_ready": False,
                "runtime_adapter_live_ready": False,
                "combined_dry_run_ready": False,
                "combined_live_ready": False,
            }
        )
        if self.coordinator is None:
            return {
                **configured,
                "provider_activation": provider_activation,
                "primary_graph_constructed": False,
                "primary_router_wired": False,
                "checkpoint_backend_postgres": False,
                "checkpoint_schema_current": False,
                "claim_store_ready": False,
                "effect_ledger_ready": False,
                "effect_executor_ready": False,
                "writer_lease_ready": False,
                "reconciliation_worker_ready": False,
                "primary_runtime_active": False,
                "new_intake_owner": "legacy",
                "legacy_new_intake_disabled": False,
                "intake_fencing_generation": 0,
                "graph_task_count": 0,
                "ambiguous_effect_count": 0,
            }
        observed = self.coordinator.readiness().as_dict()
        observed["historical_parallel_plan_hold_count"] = getattr(
            self, "_historical_parallel_hold_count", 0
        )
        observed["cancelled_plan_binding_hold_count"] = getattr(self, "_cancelled_plan_binding_hold_count", 0)
        observed["reconciliation_hold_reason"] = (
            "cancelled_parallel_plan_requires_owner_reconciliation"
            if observed["cancelled_plan_binding_hold_count"] else
            "historical_parallel_plan_requires_reconciliation"
            if observed["historical_parallel_plan_hold_count"] else None
        )
        observed["reconciliation_worker_ready"] = (
            self._reconciliation_worker_ready
        )
        observed["paid_off_primary_intake_active"] = bool(
            observed["primary_router_wired"]
            and observed["checkpoint_backend_postgres"]
            and observed["claim_store_ready"]
            and observed["writer_lease_ready"]
            and observed["legacy_new_intake_disabled"]
            and self._reconciliation_worker_ready
            and not self.settings.allow_paid_runs
        )
        from .primary_roster import core_inventory_ready
        effective_core_ready = bool(self.services and core_inventory_ready(tuple(p for p in self.services.available_provider_lanes if self.services.provider_lane_ready(p))))
        observed["primary_runtime_active"] = bool(
            self.settings.langgraph_primary_live_provider_dispatch_enabled
            and self._live_dependencies_ready
            and effective_core_ready
            and observed["primary_router_wired"]
            and observed["checkpoint_backend_postgres"]
            and observed["claim_store_ready"]
            and observed["effect_ledger_ready"]
            and observed["effect_executor_ready"]
            and observed["writer_lease_ready"]
            and observed["legacy_new_intake_disabled"]
            and self._reconciliation_worker_ready
        )
        return {
            **configured,
            **observed,
            "provider_activation": provider_activation,
        }

    def close(self) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None




# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:50 UTC | BEGIN]]
# Live approvals may cross the authenticated graph interrupt; plan-derived budget
# units, current writer fencing, and Postgres result artifacts remain mandatory.
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:50 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 22:55 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:24 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:04 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:17 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:42 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:04 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:44 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:32 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:40 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:12 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:28 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:40 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 07:05 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 07:30 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:46 UTC | END]]

# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:46 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 03:05 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:14 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 01:02 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:20 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:22 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:11 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:18 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 10:07 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:56 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:12 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:15 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:37 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:40 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 07:16 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 04:06 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 18:30 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-10 | 16:46 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-10 | 18:17 UTC | END]]

# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:04 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:46 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 09:04 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 09:30 UTC | END]]
# [[OpenAI Codex queue_runtime author | GPT-6 Astra | 2026-09-12 | 11:23 UTC | END]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-12 | 14:05 UTC | END]]
