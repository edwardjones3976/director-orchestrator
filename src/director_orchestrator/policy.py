# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:23 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:39 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:16 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:18 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:17 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 04:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 02:18 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 07:18 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 06:59 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 06:29 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 05:23 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 04:05 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 00:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 22:11 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-24 | 23:46 UTC | BEGIN]]
# [[Cursor | Cursor Grok 4.6 | 2026-08-24 | 19:32 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 14:47 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 14:01 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 13:34 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 13:02 UTC | BEGIN]]
from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

from .antigravity import (
    LANES_FOR_CAPABILITY,
    CAPABILITY_BY_KIND,
    antigravity_contract_labels,
)
from .config import Settings
from .models import (
    AntigravityDirective,
    Assignment,
    AssignmentKind,
    DirectorPlan,
    DiscussionPhase,
    MaterialDecisionDimension,
    PlanPrestageRecord,
    ProviderName,
    RiskLevel,
    WorkloadEnforcementRecord,
)
from .workload_policy import (
    AssignmentRequest,
    DirectProductionRecord,
    EnforcementMode,
    FABLE_MODEL_FLOOR,
    PrestagePacket,
    Provider,
    WorkKind,
    WorkSize,
    WorkloadRecord,
    WorkloadPolicy,
    WorkloadPolicyError,
    direct_production_provider,
    evaluate_assignment,
    is_accepted_productive_kind,
)


_GATES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("production deployment or public publishing", re.compile(r"\b(deploy|release|publish|go live|production|prod)\b", re.I)),
    ("merge or protected-branch change", re.compile(r"\b(merge|protected branch|push (?:to )?(?:main|master)|directly to main)\b", re.I)),
    ("destructive action", re.compile(r"\b(delete|destroy|drop database|truncate|purge|wipe|remove account|rewrite history|force push)\b", re.I)),
    ("purchase or paid upgrade", re.compile(r"\b(buy|purchase|subscribe|upgrade plan|charge|payment|spend)\b", re.I)),
    ("external representation", re.compile(r"\b(send|email|message|post|reply|submit)\b.{0,50}\b(customer|client|public|vendor|agency|employer|recipient)\b", re.I | re.S)),
    ("sensitive data", re.compile(r"\b(ssn|social security|passport|medical|health|financial|bank|credit card|identity|genealogy evidence|dna|authentication data)\b", re.I)),
    ("credential or permission change", re.compile(r"\b(api key|token|password|secret|credential|oauth|permission|access scope|administrator|admin access)\b", re.I)),
)

_ANTIGRAVITY_SCOPE = re.compile(
    r"\b(boilerplate|scaffold(?:ing)?|low[- ]level|mechanical|fixture generation|"
    r"alternate ideas?|alternate ideation|brainstorm(?:ing)?)\b",
    re.I,
)

_IMPLEMENTATION_SCOPE = re.compile(
    r"\b(implement(?:ation|ing)?|cod(?:e|ing)|program(?:ming)?|develop(?:ment|ing)?|"
    r"refactor(?:ing)?|patch(?:ing)?|fix(?:ing)?|scaffold(?:ing)?|boilerplate)\b",
    re.I,
)


def _debate_materiality_corpus(plan: DirectorPlan, source_text: str | None) -> str:
    """Collect owner intake plus structural plan text used only to escalate."""

    debate = plan.debate
    parts = [
        source_text or "",
        plan.project,
        plan.normalized_outcome,
    ]
    if debate is not None:
        parts.extend(
            [
                debate.question,
                debate.neutral_framing or "",
                debate.alternative_option or "",
                *debate.vote_options,
            ]
        )
    for assignment in plan.assignments:
        parts.extend([assignment.task, assignment.repository or ""])
    return "\n".join(part for part in parts if part)


def debate_tie_materiality_reasons(
    plan: DirectorPlan, *, source_text: str | None = None
) -> list[str]:
    """Return why a tie is material. Model flags may add reasons, never remove them."""

    debate = plan.debate
    if debate is None:
        return []

    reasons: list[str] = []
    if plan.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
        reasons.append(f"{plan.risk_level.value}-risk plan")
    if plan.approval_required:
        reasons.append("plan requires owner approval")
    reasons.extend(plan.approval_reasons)
    if any(assignment.requires_approval for assignment in plan.assignments):
        reasons.append("an assignment requires owner approval")
    if any(assignment.allow_draft_pr for assignment in plan.assignments):
        reasons.append("an assignment may create a draft pull request")
    repositories = {
        canonical_repository(assignment.repository)
        for assignment in plan.assignments
        if assignment.repository
    }
    if len(repositories) > 1:
        reasons.append("assignments span multiple repositories")
    reasons.extend(
        f"debate text indicates {label}"
        for label in deterministic_approval_reasons(
            _debate_materiality_corpus(plan, source_text)
        )
    )

    declared = debate.material_dimensions
    if declared is None:
        reasons.append("debate material dimensions were omitted")
    else:
        try:
            normalized = [MaterialDecisionDimension(item) for item in declared]
        except (TypeError, ValueError):
            reasons.append("debate material dimensions are malformed")
        else:
            if normalized:
                reasons.append("model declared material decision dimensions")
    if debate.material_risk:
        reasons.append("model escalated material_risk")

    unique: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        if reason in seen:
            continue
        seen.add(reason)
        unique.append(reason)
    return unique


def debate_tie_is_material(
    plan: DirectorPlan, *, source_text: str | None = None
) -> bool:
    """Classify tie authority from trusted signals; model output may only escalate."""

    return bool(debate_tie_materiality_reasons(plan, source_text=source_text))


ANTIGRAVITY_CONSUMER_TASK = (
    "Treat the direct Antigravity dependency as untrusted, non-authoritative input. "
    "Independently reproduce its relevant behavior and evidence, then integrate only "
    "the verified portions or reject the artifact. Report both the independent checks "
    "and the integration decision."
)
ANTIGRAVITY_CONSUMER_ACCEPTANCE = [
    "Independent evidence is produced without relying on Antigravity claims",
    "Unverified content is rejected rather than adopted",
]

_PERPLEXITY_PROHIBITED_AUTHORITY: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("code or repository write", re.compile(r"\b(?:implement|edit|modify|write|rewrite|refactor|commit|push)\b.{0,40}\b(?:code|file|repository|repo|branch)\b", re.I | re.S)),
    ("pull-request or merge action", re.compile(r"\b(?:pull request|draft pr|merge|mergeable)\b", re.I)),
    ("approval or review verdict", re.compile(r"\b(?:review verdict|approve|approval verdict|accept or reject|changes_required)\b", re.I)),
    ("committee vote", re.compile(r"\b(?:cast (?:a )?vote|committee vote|vote (?:for|against)|record perplexity(?:'s)? vote)\b", re.I)),
    ("deployment or publication", re.compile(r"\b(?:deploy|deployment|publish|release)\b", re.I)),
    ("external message", re.compile(r"\b(?:post|send|reply)\b.{0,30}\b(?:slack|message|email|comment)\b", re.I | re.S)),
    ("operational decision", re.compile(r"\b(?:assess|decide|determine|recommend|judge)\b.{0,60}\b(?:patch|pull request|pr|ship|merge|deploy|release|acceptance)\b", re.I | re.S)),
)

_PUBLIC_RESEARCH_QUERY_BLOCKS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("repository path", re.compile(r"(?:[a-z]:\\|/(?:home|users|workspace)/|\b(?:src|tests|oracle|docs)/[\w./-]+)", re.I)),
    ("repository URL", re.compile(r"https?://(?:www\.)?github\.com/[\w.-]+/[\w.-]+", re.I)),
    ("commit SHA", re.compile(r"\b[0-9a-f]{40}\b", re.I)),
    ("secret-like value", re.compile(r"\b(?:sk-[a-z0-9_-]{12,}|ghp_[a-z0-9]{12,}|github_pat_[a-z0-9_]{12,}|xox[baprs]-[a-z0-9-]{12,})\b", re.I)),
    ("diff or source excerpt", re.compile(r"(?:diff --git|@@ -\d|```(?:python|javascript|typescript|diff|json)?\s)", re.I)),
    ("private-context language", re.compile(r"\b(?:private|internal|unpublished|our repository|our code|attached patch)\b", re.I)),
    ("email address", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
    ("UUID", re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)),
    ("Slack identifier", re.compile(r"\b[CUW](?=[A-Z0-9]{8,}\b)(?=[A-Z0-9]*\d)[A-Z0-9]{8,}\b", re.I)),
)

def deterministic_approval_reasons(text: str) -> list[str]:
    return [label for label, pattern in _GATES if pattern.search(text)]


def canonical_repository(value: str) -> str:
    candidate = value.strip().rstrip("/")
    parsed = urlparse(candidate)
    if parsed.scheme and parsed.netloc:
        if parsed.netloc.lower() != "github.com":
            return candidate.lower()
        candidate = parsed.path.strip("/")
    if candidate.lower().startswith("github.com/"):
        candidate = candidate[len("github.com/") :]
    if candidate.lower().endswith(".git"):
        candidate = candidate[:-4]
    return candidate.strip("/").lower()


def antigravity_assignment_errors(assignment: Assignment) -> list[str]:
    """Deterministic subordinate-lane admission for one Antigravity work assignment.

    What may be transmitted is decided entirely by the closed contract in
    `antigravity.py`, which reads typed fields only and never forwards prose. This
    function adds one plan-coherence requirement the contract cannot express: the
    task the Director wrote for its own review must actually describe subordinate
    work, so an in-lane `kind` attached to an out-of-lane task is still refused.

    That check is routing hygiene on text that stays inside this process. It is not
    a disclosure control, and no disclosure control is attempted on prose anywhere:
    prose is simply never sent.

    Antigravity debate assignments use the separate Director-invited contract in
    `antigravity.py` and never enter this subordinate-work admission path.
    """
    # The admitted kinds live in CAPABILITY_BY_KIND, which is also what selects the
    # outbound capability. Keeping one table means the lane and the wire cannot drift.
    errors: list[str] = []
    if not _ANTIGRAVITY_SCOPE.search(assignment.task):
        errors.append(
            "requires explicit subordinate boilerplate, scaffolding, low-level, "
            "mechanical, or alternate-ideation scope"
        )
    errors.extend(antigravity_contract_labels(assignment))
    return sorted(set(errors))


def default_antigravity_directive(kind: AssignmentKind) -> AntigravityDirective | None:
    """The deterministic lane the runtime binds when the Director omits one."""
    capability = CAPABILITY_BY_KIND.get(kind)
    if capability is None:
        return None
    lane = sorted(LANES_FOR_CAPABILITY[capability])[0]
    return AntigravityDirective(lane=lane)


def _antigravity_work_sequences(plan: DirectorPlan) -> set[int]:
    return {
        item.sequence
        for item in plan.assignments
        if item.agent == ProviderName.GEMINI
        and item.discussion_phase == DiscussionPhase.WORK
    }


def _is_antigravity_consumer(assignment: Assignment) -> bool:
    return (
        assignment.kind == AssignmentKind.IMPLEMENTATION
        and assignment.task == ANTIGRAVITY_CONSUMER_TASK
        and assignment.acceptance == ANTIGRAVITY_CONSUMER_ACCEPTANCE
    )


def _workload_provider(provider: ProviderName) -> Provider:
    try:
        return Provider(provider.value)
    except ValueError as exc:
        raise WorkloadPolicyError(
            f"provider is outside the workload ledger: {provider.value}"
        ) from exc


def _work_kind(assignment: Assignment) -> WorkKind:
    if assignment.kind in {
        AssignmentKind.IMPLEMENTATION,
        AssignmentKind.BOILERPLATE,
        AssignmentKind.SCAFFOLDING,
        AssignmentKind.LOW_LEVEL_IMPLEMENTATION,
    }:
        return WorkKind.IMPLEMENTATION
    if assignment.kind in {
        AssignmentKind.INDEPENDENT_REVIEW,
        AssignmentKind.EXACT_SHA_REVIEW,
    }:
        return WorkKind.REVIEW
    if assignment.agent == ProviderName.PERPLEXITY:
        return WorkKind.RESEARCH
    if _IMPLEMENTATION_SCOPE.search(assignment.task):
        return WorkKind.IMPLEMENTATION
    if assignment.kind == AssignmentKind.RESEARCH:
        return WorkKind.RESEARCH
    return WorkKind.COORDINATION


def trusted_work_kind(assignment: Assignment) -> WorkKind:
    """Return the single deterministic workload classification used at every gate."""

    return _work_kind(assignment)


_WORK_SIZE_RANK = {
    WorkSize.SIMPLE: 0,
    WorkSize.SHORT: 1,
    WorkSize.MEDIUM: 2,
    WorkSize.LONG: 3,
}

_WORK_SIZE_EFFORT_POINTS = {
    WorkSize.SIMPLE: 1,
    WorkSize.SHORT: 2,
    WorkSize.MEDIUM: 5,
    WorkSize.LONG: 8,
}


def _trusted_v1_assignment(assignment: Assignment) -> Assignment:
    """Derive trusted workload fields without accepting model under-classification."""

    kind = _work_kind(assignment)
    if kind == WorkKind.IMPLEMENTATION:
        # This minimum is provider-neutral. It preserves deterministic effort
        # sizing without implying that medium or long work belongs to Claude.
        # Antigravity remains short because a primary-team consumer must reproduce
        # or reject its non-authoritative output.
        minimum_size = (
            WorkSize.SHORT
            if assignment.agent == ProviderName.GEMINI
            else WorkSize.MEDIUM
        )
    elif assignment.agent == ProviderName.OPENAI and kind == WorkKind.COORDINATION:
        # Only genuinely simple work kept local by OpenAI may stay in this lane.
        minimum_size = WorkSize.SIMPLE
    else:
        minimum_size = WorkSize.SHORT

    effective_size = assignment.workload_size
    if _WORK_SIZE_RANK[effective_size] < _WORK_SIZE_RANK[minimum_size]:
        effective_size = minimum_size

    updates: dict[str, object] = {
        "workload_size": effective_size,
        # Effort is a deterministic consequence of trusted size, never model input.
        "effort_points": _WORK_SIZE_EFFORT_POINTS[effective_size],
    }
    if assignment.agent == ProviderName.CLAUDE and effective_size in {
        WorkSize.MEDIUM,
        WorkSize.LONG,
    }:
        updates["model_label"] = FABLE_MODEL_FLOOR
    return assignment.model_copy(update=updates)


def _canonical_prestage_record(record: PlanPrestageRecord) -> PlanPrestageRecord:
    """Rebuild a proposed packet through the immutable canonical contract."""

    packet = PrestagePacket(
        packet_id=record.packet_id,
        goal=record.goal,
        terminal_condition=record.terminal_condition,
        repository=canonical_repository(record.repository),
        worktree=record.worktree,
        branch=record.branch,
        base_sha=record.base_sha,
        candidate_sha=record.candidate_sha,
        producer=_workload_provider(record.producer),
        independent_reviewer=_workload_provider(record.independent_reviewer),
        owned_paths=record.owned_paths,
        prohibited_paths=record.prohibited_paths,
        required_tests=record.required_tests,
        required_security_evidence=record.required_security_evidence,
        budget_units=record.budget_units,
        timeout_seconds=record.timeout_seconds,
        retry_limit=record.retry_limit,
        merge_eligibility_conditions=record.merge_eligibility_conditions,
        stopping_conditions=record.stopping_conditions,
    )
    return PlanPrestageRecord.model_validate(packet.to_dict())


def _material_author_agents(
    by_sequence: dict[int, Assignment], reviewed: Assignment
) -> set[ProviderName]:
    """Return every provider whose output is material input to ``reviewed``.

    A dependency that is absent from the plan contributes nothing here; the
    generic dependency-existence check reports it separately.
    """

    authors: set[ProviderName] = set()
    seen: set[int] = set()
    pending = list(reviewed.depends_on)
    while pending:
        sequence = pending.pop()
        if sequence in seen:
            continue
        seen.add(sequence)
        dependency = by_sequence.get(sequence)
        if dependency is None:
            continue
        authors.add(dependency.agent)
        pending.extend(dependency.depends_on)
    return authors


def _independent_review_binding_errors(plan: DirectorPlan) -> set[str]:
    """Bind each independent review to the exact work it is budgeted to examine.

    Without this an independent-review assignment floats free of any artifact:
    it can declare no dependency at all, depend on unrelated work, or be routed
    back to the provider that produced — or materially authored — the very thing
    it is supposed to check independently.
    """

    errors: set[str] = set()
    by_sequence = {item.sequence: item for item in plan.assignments}
    implementation_sequences = {
        item.sequence
        for item in plan.assignments
        if _work_kind(item) is WorkKind.IMPLEMENTATION
    }
    for review in plan.assignments:
        if review.kind != AssignmentKind.INDEPENDENT_REVIEW:
            continue
        bound = [
            sequence for sequence in review.depends_on if sequence in by_sequence
        ]
        if len(review.depends_on) != 1 or len(bound) != 1:
            errors.add(
                f"assignment {review.sequence}: independent review must depend on "
                "exactly one existing assignment it reviews"
            )
            continue
        reviewed = by_sequence[bound[0]]
        if (
            implementation_sequences
            and reviewed.sequence not in implementation_sequences
        ):
            errors.add(
                f"assignment {review.sequence}: independent review must bind the "
                "exact implementation assignment it reviews"
            )
        if review.agent == reviewed.agent:
            errors.add(
                f"assignment {review.sequence}: the producer of assignment "
                f"{reviewed.sequence} cannot review its own work"
            )
        elif review.agent in _material_author_agents(by_sequence, reviewed):
            errors.add(
                f"assignment {review.sequence}: a material author of assignment "
                f"{reviewed.sequence} cannot review it"
            )
    return errors


def _v1_workload_plan_errors(plan: DirectorPlan) -> set[str]:
    """Validate v1 routing without manufacturing accepted production credit."""

    if plan.workload_policy_version != "v1":
        return set()

    errors: set[str] = set()
    direct_history: list[DirectProductionRecord] = []
    overall_history: list[WorkloadRecord] = []
    for assignment in sorted(plan.assignments, key=lambda item: item.sequence):
        try:
            provider = _workload_provider(assignment.agent)
        except WorkloadPolicyError as exc:
            errors.add(f"assignment {assignment.sequence}: {exc}")
            continue
        request = AssignmentRequest(
            assignment_id=f"{plan.project}:{assignment.sequence}",
            provider=provider,
            kind=_work_kind(assignment),
            size=assignment.workload_size,
            model_label=assignment.model_label,
            effort_units=assignment.effort_points,
        )
        decision = evaluate_assignment(
            request,
            history=overall_history,
            direct_production_history=direct_history,
            mode=EnforcementMode.ENFORCED,
        )
        errors.update(
            f"assignment {assignment.sequence}: workload policy {violation}"
            for violation in decision.violations
        )
        # Total effort is prospective and assignment ordered. Every proposed
        # assignment, including non-productive work, becomes history for the
        # next assignment in this exact plan.
        overall_history.append(
            WorkloadRecord(
                assignment_id=request.assignment_id,
                provider=provider,
                effort_units=assignment.effort_points,
            )
        )
        direct_provider = direct_production_provider(provider)
        if direct_provider is not None and is_accepted_productive_kind(
            provider, request.kind
        ):
            direct_history.append(
                DirectProductionRecord(
                    assignment_id=request.assignment_id,
                    provider=direct_provider,
                    effort_units=assignment.effort_points,
                )
            )

    # A genuinely simple request that needs no external opinion may stay local.
    # Every short, medium, or long provider plan is team work.
    team = {
        item.agent
        for item in plan.assignments
        if item.agent not in {ProviderName.OWNER, ProviderName.OPENAI}
    }
    nontrivial = any(
        item.workload_size.value != "simple" for item in plan.assignments
    )
    if nontrivial and len(team) < 2:
        errors.add(
            "v1 provider plans require at least two distinct team providers; "
            "only genuinely simple local work may remain with OpenAI alone"
        )

    errors.update(_independent_review_binding_errors(plan))

    if len(set(plan.research_evidence)) != len(plan.research_evidence):
        errors.add("research evidence declarations must be unique")
    if plan.research_evidence:
        research_assignments = [
            item
            for item in plan.assignments
            if item.agent == ProviderName.PERPLEXITY
            and item.kind == AssignmentKind.RESEARCH
            and item.public_research_query
        ]
        if not research_assignments:
            evidence = ", ".join(item.value for item in plan.research_evidence)
            errors.add(
                "declared public evidence requires a bounded Perplexity research "
                f"assignment: {evidence}"
            )

    prestage = plan.prestage
    if prestage is None:
        errors.add("v1 workload plans require an immutable pre-stage packet")
        return errors
    try:
        canonical = _canonical_prestage_record(prestage)
    except (ValueError, WorkloadPolicyError) as exc:
        errors.add(f"pre-stage packet is invalid: {exc}")
        return errors
    if prestage.digest is None:
        errors.add("pre-stage packet requires its canonical digest")
    elif prestage.digest != canonical.digest:
        errors.add("pre-stage packet digest does not match its canonical contents")
    if prestage.goal != plan.normalized_outcome:
        errors.add("pre-stage goal must exactly match the normalized plan outcome")
    if prestage.terminal_condition != "\n".join(plan.final_acceptance):
        errors.add("pre-stage terminal condition must exactly bind final acceptance")
    if prestage.budget_units < sum(item.effort_points for item in plan.assignments):
        errors.add("pre-stage budget units do not cover planned assignment effort")
    over_prestage_retry_budget = [
        item.sequence
        for item in plan.assignments
        if item.retry_limit > prestage.retry_limit
    ]
    if over_prestage_retry_budget:
        errors.add(
            "assignment retry limits exceed the digest-bound pre-stage retry limit: "
            + ", ".join(str(sequence) for sequence in over_prestage_retry_budget)
        )

    repository_scope: list[tuple[str, str]] = []
    repository_scope_unbound = False
    for assignment in plan.assignments:
        if assignment.repository is None:
            continue
        repository = canonical_repository(assignment.repository)
        if not repository:
            repository_scope_unbound = True
            continue
        repository_scope.append((repository, assignment.starting_ref.strip()))
    repository_binding_required = any(
        assignment.agent
        in {ProviderName.CLAUDE, ProviderName.CURSOR, ProviderName.GROK}
        or assignment.allow_draft_pr
        for assignment in plan.assignments
    )
    if repository_scope_unbound or (repository_binding_required and not repository_scope):
        errors.add("v1 pre-stage repository scope is unbound")
    elif repository_scope:
        repositories = {repository for repository, _ in repository_scope}
        if len(repositories) > 1:
            errors.add(
                "v1 pre-stage repository scope spans multiple canonical assignment repositories"
            )
        elif canonical_repository(prestage.repository) != next(iter(repositories)):
            errors.add(
                "pre-stage repository must match the single canonical assignment repository"
            )

        starting_refs = {starting_ref for _, starting_ref in repository_scope}
        if not starting_refs or "" in starting_refs:
            errors.add("v1 pre-stage starting-ref scope is unbound")
        elif len(starting_refs) > 1:
            errors.add(
                "v1 pre-stage starting-ref scope spans multiple assignment starting refs"
            )
        elif prestage.branch != next(iter(starting_refs)):
            errors.add(
                "pre-stage branch must match the single assignment starting ref"
            )
    if prestage.candidate_sha is not None:
        errors.add("initial pre-stage candidate SHA must be absent before production")

    assignment_agents = {item.agent for item in plan.assignments}
    if prestage.producer not in assignment_agents:
        errors.add("pre-stage producer is not assigned in the plan")
    review_agents = {
        item.agent
        for item in plan.assignments
        if item.kind
        in {AssignmentKind.INDEPENDENT_REVIEW, AssignmentKind.EXACT_SHA_REVIEW}
    }
    if prestage.independent_reviewer not in review_agents:
        errors.add("pre-stage independent reviewer lacks a typed review assignment")
    return errors


def _workload_settings_errors(settings: Settings) -> set[str]:
    if not settings.workload_policy_enabled:
        return set()
    errors: set[str] = set()
    if settings.workload_policy_mode not in {
        EnforcementMode.ENFORCED.value,
        EnforcementMode.SHADOW.value,
    }:
        errors.add("workload policy mode must be enforced or shadow")
    policy = WorkloadPolicy.default()
    if settings.workload_policy_window_assignments != policy.rolling_window_size:
        errors.add("workload policy rolling window must be 20 assignments")
    if settings.workload_policy_shadow_assignments != 5:
        errors.add("workload policy shadow rehearsal must be five assignments")
    return errors


def _workload_enforcement_record(
    plan: DirectorPlan, settings: Settings
) -> WorkloadEnforcementRecord | None:
    if plan.workload_policy_version != "v1":
        return None
    if not settings.workload_policy_enabled:
        return WorkloadEnforcementRecord(
            mode="disabled",
            outcome="disabled",
            allowed=True,
            would_block=False,
        )
    violations = tuple(
        sorted(_v1_workload_plan_errors(plan) | _workload_settings_errors(settings))
    )
    mode = settings.workload_policy_mode
    if mode == EnforcementMode.SHADOW.value:
        return WorkloadEnforcementRecord(
            mode="shadow",
            outcome="shadow_would_block" if violations else "allow",
            allowed=True,
            would_block=bool(violations),
            violations=violations,
        )
    return WorkloadEnforcementRecord(
        mode="enforced",
        outcome="block" if violations else "allow",
        allowed=not violations,
        would_block=bool(violations),
        violations=violations,
    )


def execution_policy_errors(plan: DirectorPlan, settings: Settings) -> list[str]:
    errors: set[str] = set()
    workload_record = _workload_enforcement_record(plan, settings)
    if workload_record is not None:
        if (
            workload_record.mode == "enforced"
            and plan.workload_enforcement != workload_record
        ):
            errors.add("workload enforcement record is absent, stale, or untrusted")
        if workload_record.mode == "enforced":
            errors.update(workload_record.violations)
    if (
        plan.prestage is not None
        and plan.prestage.packet_id == "dry-run-preview"
        and settings.allow_paid_runs
    ):
        errors.add("dry-run preview packets cannot authorize paid provider dispatch")
    if tuple(settings.primary_team) != ("openai", "claude", "cursor", "grok"):
        errors.add("primary team must be exactly openai, claude, cursor, grok")
    allowed_projects = {item.strip().lower() for item in settings.project_allowlist}
    project = plan.project.strip().lower()
    if not allowed_projects:
        errors.add("project allowlist is not configured")
    elif project not in allowed_projects:
        errors.add(f"project is not allowlisted: {plan.project}")

    sequences = [item.sequence for item in plan.assignments]
    sequence_set = set(sequences)
    if len(sequence_set) != len(sequences):
        errors.add("assignment sequences must be unique")

    discussion_assignments = {
        item.sequence: item
        for item in plan.assignments
        if item.discussion_phase != DiscussionPhase.WORK
    }
    if plan.debate is None and discussion_assignments:
        errors.add("discussion assignments require a bounded debate plan")
    if plan.debate is not None:
        debate = plan.debate
        position_sequences = debate.position_assignments
        rebuttal_sequences = debate.rebuttal_assignments
        if len(set(position_sequences)) != len(position_sequences):
            errors.add("debate position assignments must be unique")
        if len(set(rebuttal_sequences)) != len(rebuttal_sequences):
            errors.add("debate rebuttal assignments must be unique")
        if set(position_sequences).intersection(rebuttal_sequences):
            errors.add("debate position and rebuttal assignments must be disjoint")
        if len({option.strip().lower() for option in debate.vote_options}) != len(
            debate.vote_options
        ):
            errors.add("debate vote options must be unique")
        normalized_options = {
            option.strip().lower(): option for option in debate.vote_options
        }
        if debate.neutral_framing is None:
            errors.add("debate must record a neutral framing")
        if (
            debate.alternative_option is not None
            and debate.alternative_option.strip().lower() not in normalized_options
        ):
            errors.add("debate alternative option must be one configured vote option")
        initial = debate.openai_initial_position
        if initial is None:
            errors.add("debate must record OpenAI's independent initial position")
        elif not initial.abstain and (
            initial.provisional_option is None
            or initial.provisional_option.strip().lower() not in normalized_options
        ):
            errors.add(
                "OpenAI preliminary position selected an unconfigured debate option"
            )
        if debate.tie_judgment_rubric is None:
            errors.add("debate must predeclare an ordinary-tie judgment rubric")
        if debate.material_dimensions is None:
            errors.add("debate must declare its material decision dimensions")
        expected_sequences = set(position_sequences + rebuttal_sequences)
        if set(discussion_assignments) != expected_sequences:
            errors.add("debate assignment lists must exactly match discussion assignments")
        # Antigravity is procedurally equal to Claude and Cursor *after* the
        # Director has persisted this bounded debate plan. It can never create
        # one: the invitation is a plan artifact, and `antigravity.py` refuses a
        # debate turn whose invitation does not match the persisted assignment.
        # OpenAI remains the synthesizing Director and Perplexity stays
        # research-only, so neither is seatable here.
        eligible = {
            ProviderName.CLAUDE,
            ProviderName.CURSOR,
            ProviderName.GEMINI,
            ProviderName.GROK,
        }
        position_providers: set[ProviderName] = set()
        positions = set(position_sequences)
        for sequence in position_sequences:
            assignment = discussion_assignments.get(sequence)
            if assignment is None:
                errors.add(f"debate position assignment {sequence} does not exist")
                continue
            if assignment.discussion_phase != DiscussionPhase.POSITION:
                errors.add(f"assignment {sequence}: debate position must use position phase")
            if assignment.discussion_round != 0:
                errors.add(
                    f"assignment {sequence}: initial position must use discussion round 0"
                )
            if assignment.agent not in eligible:
                errors.add(f"assignment {sequence}: provider is not eligible to debate or vote")
            if assignment.agent in position_providers:
                errors.add(f"assignment {sequence}: each debate provider gets one initial position")
            position_providers.add(assignment.agent)
            if set(discussion_assignments).intersection(assignment.depends_on):
                errors.add(f"assignment {sequence}: initial positions must remain independent")
        rebuttal_providers: set[ProviderName] = set()
        for sequence in rebuttal_sequences:
            assignment = discussion_assignments.get(sequence)
            if assignment is None:
                errors.add(f"debate rebuttal assignment {sequence} does not exist")
                continue
            if assignment.discussion_phase != DiscussionPhase.REBUTTAL:
                errors.add(f"assignment {sequence}: debate rebuttal must use rebuttal phase")
            if not 1 <= assignment.discussion_round <= debate.max_rebuttal_rounds:
                errors.add(
                    f"assignment {sequence}: rebuttal round must be between 1 and "
                    f"{debate.max_rebuttal_rounds}"
                )
            if assignment.agent not in position_providers:
                errors.add(f"assignment {sequence}: rebuttal provider lacks an initial position")
            if assignment.agent in rebuttal_providers:
                errors.add(f"assignment {sequence}: each debate provider gets one rebuttal")
            rebuttal_providers.add(assignment.agent)
            if not positions.issubset(assignment.depends_on):
                errors.add(
                    f"assignment {sequence}: rebuttal must depend on every initial position"
                )
            if set(rebuttal_sequences).intersection(assignment.depends_on):
                errors.add(
                    f"assignment {sequence}: rebuttal cannot depend on another rebuttal"
                )
        if len(position_providers) < 2:
            errors.add("a debate requires at least two eligible providers")
        if rebuttal_providers != position_providers:
            errors.add("every initial-position provider must receive one rebuttal")

    allowed_repositories = {
        canonical_repository(item) for item in settings.repository_allowlist
    }
    allowed_refs = set(settings.allowed_starting_refs)
    for assignment in plan.assignments:
        if assignment.agent == ProviderName.OPENAI:
            errors.add(
                f"assignment {assignment.sequence}: OpenAI is the Director runtime and cannot be an assignment target"
            )
        if assignment.agent == ProviderName.CURSOR and not assignment.repository:
            errors.add(f"assignment {assignment.sequence}: Cursor requires an explicit repository")
        if (
            assignment.discussion_phase == DiscussionPhase.WORK
            and assignment.discussion_round != 0
        ):
            errors.add(
                f"assignment {assignment.sequence}: ordinary work must use discussion round 0"
            )
        if (
            assignment.antigravity_directive is not None
            and not (
                assignment.agent == ProviderName.GEMINI
                and assignment.discussion_phase == DiscussionPhase.WORK
            )
        ):
            errors.add(
                f"assignment {assignment.sequence}: antigravity directive is reserved "
                "for Antigravity work assignments"
            )
        if (
            assignment.agent == ProviderName.GEMINI
            and assignment.discussion_phase == DiscussionPhase.WORK
        ):
            for error in antigravity_assignment_errors(assignment):
                errors.add(f"assignment {assignment.sequence}: Antigravity {error}")
            consumers = [
                later
                for later in plan.assignments
                if later.sequence > assignment.sequence
                and later.agent in {
                    ProviderName.CLAUDE,
                    ProviderName.CURSOR,
                    ProviderName.GROK,
                }
                and later.discussion_phase == DiscussionPhase.WORK
                and assignment.sequence in later.depends_on
            ]
            if not consumers:
                errors.add(
                    f"assignment {assignment.sequence}: Antigravity output requires a later "
                    "Claude, Cursor, or Grok integration assignment with a direct dependency"
                )
            elif not any(_is_antigravity_consumer(item) for item in consumers):
                errors.add(
                    f"assignment {assignment.sequence}: Antigravity output requires a later "
                    "Claude, Cursor, or Grok implementation assignment that explicitly independently "
                    "verifies and integrates or rejects the artifact"
                )
        if (
            assignment.agent == ProviderName.GEMINI
            and assignment.discussion_phase != DiscussionPhase.WORK
        ):
            # An invited debate turn carries prose, so every authority-bearing and
            # repository-bearing field must stay unset. Authority is refused by the
            # fixed denied-authority block on the wire; these are the plan-side
            # structural denials that cannot be expressed inside the contract.
            if assignment.kind != AssignmentKind.GENERAL:
                errors.add(
                    f"assignment {assignment.sequence}: Antigravity debate is advisory general work only"
                )
            if assignment.repository:
                errors.add(
                    f"assignment {assignment.sequence}: Antigravity debate cannot receive repository context"
                )
            if assignment.starting_ref != "main":
                errors.add(
                    f"assignment {assignment.sequence}: Antigravity debate cannot select a starting ref"
                )
            if assignment.allow_draft_pr:
                errors.add(
                    f"assignment {assignment.sequence}: Antigravity debate cannot create a draft pull request"
                )
            if assignment.public_research_query is not None:
                errors.add(
                    f"assignment {assignment.sequence}: Antigravity debate cannot receive a public research query"
                )
        if assignment.agent == ProviderName.GROK:
            if assignment.public_research_query is not None:
                errors.add(
                    f"assignment {assignment.sequence}: Grok has no web-grounded research lane"
                )
        if assignment.agent == ProviderName.PERPLEXITY:
            if assignment.kind != AssignmentKind.RESEARCH:
                errors.add(
                    f"assignment {assignment.sequence}: Perplexity is research-only and requires research kind"
                )
            if not assignment.requires_approval:
                errors.add(
                    f"assignment {assignment.sequence}: Perplexity research requires exact-query owner approval"
                )
            if not assignment.public_research_query:
                errors.add(
                    f"assignment {assignment.sequence}: Perplexity requires a bounded public research query"
                )
            if assignment.repository:
                errors.add(
                    f"assignment {assignment.sequence}: Perplexity research cannot receive repository context"
                )
            if assignment.allow_draft_pr:
                errors.add(
                    f"assignment {assignment.sequence}: Perplexity research cannot create a draft pull request"
                )
            scope_text = "\n".join([assignment.task, *assignment.acceptance])
            for label, pattern in _PERPLEXITY_PROHIBITED_AUTHORITY:
                if pattern.search(scope_text):
                    errors.add(
                        f"assignment {assignment.sequence}: Perplexity research cannot receive {label} authority"
                    )
            if assignment.public_research_query:
                for label, pattern in _PUBLIC_RESEARCH_QUERY_BLOCKS:
                    if pattern.search(assignment.public_research_query):
                        errors.add(
                            f"assignment {assignment.sequence}: Perplexity public research query contains {label}"
                        )
        elif assignment.public_research_query is not None:
            errors.add(
                f"assignment {assignment.sequence}: public research query is reserved for Perplexity"
            )
        if assignment.allow_draft_pr and not assignment.requires_approval:
            errors.add(
                f"assignment {assignment.sequence}: draft PR creation requires assignment approval"
            )
        if assignment.allow_draft_pr:
            reviewers = [
                candidate
                for candidate in plan.assignments
                if candidate.depends_on == [assignment.sequence]
                and candidate.agent in {
                    ProviderName.CLAUDE,
                    ProviderName.CURSOR,
                    ProviderName.GROK,
                }
                and candidate.agent != assignment.agent
            ]
            if len(reviewers) != 1:
                errors.add(
                    f"assignment {assignment.sequence}: draft PR work requires exactly one pre-budgeted independent Claude, Cursor, or Grok review"
                )
            elif reviewers[0].allow_draft_pr:
                errors.add(
                    f"assignment {reviewers[0].sequence}: review assignment cannot create a draft pull request"
                )
        if assignment.repository:
            repository = canonical_repository(assignment.repository)
            if not allowed_repositories:
                errors.add("repository allowlist is not configured")
            elif repository not in allowed_repositories:
                errors.add(
                    f"assignment {assignment.sequence}: repository is not allowlisted: {repository}"
                )
            if not allowed_refs:
                errors.add("starting-ref allowlist is not configured")
            elif assignment.starting_ref not in allowed_refs:
                if assignment.starting_ref.startswith("cursor/"):
                    errors.add(
                        f"assignment {assignment.sequence}: starting ref is a Cursor "
                        "working branch that is deleted after merge: "
                        f"{assignment.starting_ref}"
                    )
                else:
                    errors.add(
                        f"assignment {assignment.sequence}: starting ref is not allowlisted: {assignment.starting_ref}"
                    )
        for dependency in assignment.depends_on:
            if dependency not in sequence_set:
                errors.add(
                    f"assignment {assignment.sequence}: dependency {dependency} does not exist"
                )
            elif dependency >= assignment.sequence:
                errors.add(
                    f"assignment {assignment.sequence}: dependency {dependency} must be an earlier assignment"
                )
    return sorted(errors)


#: Deterministic floor for one Director acceptance-review call. A plan makes one
#: review per assignment attempt, so reserving zero under-reserves the envelope by
#: exactly that many calls and lets a plan pass the budget gate it should fail.
MINIMUM_OPENAI_REVIEW_COST_USD = 0.01


def estimated_plan_cost_usd(
    plan: DirectorPlan,
    *,
    openai_review_cost_usd: float = MINIMUM_OPENAI_REVIEW_COST_USD,
) -> float:
    """Return the complete prospective cost envelope for one plan generation."""

    attempt_count = 1 + plan.max_revisions
    estimated_cost = sum(
        item.estimated_cost_usd * attempt_count for item in plan.assignments
    )
    review_count = len(plan.assignments) * attempt_count
    # The floor applies even when a caller passes a lower or negative estimate:
    # this is a reservation, and under-reserving is the failure mode that matters.
    estimated_cost += review_count * max(
        MINIMUM_OPENAI_REVIEW_COST_USD, openai_review_cost_usd
    )
    if plan.debate is not None:
        estimated_cost += plan.debate.estimated_counter_bias_cost_usd
        estimated_cost += plan.debate.estimated_synthesis_cost_usd
        estimated_cost += plan.debate.estimated_tie_judgment_cost_usd
    return estimated_cost


def trusted_openai_debate_phase_cost_usd(
    model_estimate_usd: float, settings: Settings
) -> float:
    """Apply the trusted operator floor without reducing a conservative estimate."""

    return max(model_estimate_usd, settings.openai_debate_turn_estimated_cost_usd)


def enforce_plan(
    plan: DirectorPlan,
    source_text: str,
    settings: Settings,
    *,
    activate_workload_policy: bool = False,
) -> DirectorPlan:
    safe = deepcopy(plan)
    # The live engine explicitly marks a newly generated plan. Persisted plans and
    # focused pre-policy unit callers retain the null legacy marker, while omission
    # at the live generation boundary cannot opt out of default-on enforcement.
    if activate_workload_policy:
        safe.workload_policy_version = "v1"
    workload_v1 = safe.workload_policy_version == "v1"
    # The enforcement outcome is deterministic trusted metadata, never a value the
    # planning model may preserve or select.
    safe.workload_enforcement = None
    safe.max_hops = min(safe.max_hops, settings.max_agent_hops)
    safe.max_revisions = min(safe.max_revisions, settings.max_revisions)
    if safe.debate is not None:
        safe.debate = safe.debate.model_copy(
            update={
                "estimated_counter_bias_cost_usd": (
                    trusted_openai_debate_phase_cost_usd(
                        safe.debate.estimated_counter_bias_cost_usd, settings
                    )
                ),
                "estimated_synthesis_cost_usd": (
                    trusted_openai_debate_phase_cost_usd(
                        safe.debate.estimated_synthesis_cost_usd, settings
                    )
                ),
                "estimated_tie_judgment_cost_usd": (
                    trusted_openai_debate_phase_cost_usd(
                        safe.debate.estimated_tie_judgment_cost_usd, settings
                    )
                ),
            }
        )

    reasons = set(safe.approval_reasons)
    reasons.update(deterministic_approval_reasons(source_text))
    if safe.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
        reasons.add(f"{safe.risk_level.value}-risk plan")
    estimated_cost = estimated_plan_cost_usd(
        safe,
        openai_review_cost_usd=settings.openai_review_estimated_cost_usd,
    )
    if estimated_cost > settings.max_task_budget_usd:
        reasons.add("estimated cost exceeds task budget")

    safe.approval_reasons = sorted(reasons)
    safe.approval_required = bool(reasons) or safe.approval_required
    if safe.approval_required:
        safe.assignments = [
            item.model_copy(update={"requires_approval": True})
            if item.requires_approval or reasons
            else item
            for item in safe.assignments
        ]
    safe.assignments = [
        item.model_copy(update={"requires_approval": True})
        if item.agent == ProviderName.PERPLEXITY
        else item
        for item in safe.assignments
    ]
    # Bind the outbound fixture lane deterministically when the Director omitted it,
    # the same way the integration wording below is bound. A missing directive is
    # still an admission error, so a plan that skips enforce_plan fails closed.
    safe.assignments = [
        item.model_copy(
            update={"antigravity_directive": default_antigravity_directive(item.kind)}
        )
        if item.agent == ProviderName.GEMINI
        and item.discussion_phase == DiscussionPhase.WORK
        and item.antigravity_directive is None
        else item
        for item in safe.assignments
    ]
    antigravity_work = _antigravity_work_sequences(safe)
    if antigravity_work:
        safe.assignments = [
            item.model_copy(
                update={
                    "kind": AssignmentKind.IMPLEMENTATION,
                    "task": ANTIGRAVITY_CONSUMER_TASK,
                    "acceptance": list(ANTIGRAVITY_CONSUMER_ACCEPTANCE),
                }
            )
            if item.agent
            in {ProviderName.CLAUDE, ProviderName.CURSOR, ProviderName.GROK}
            and item.discussion_phase == DiscussionPhase.WORK
            and set(item.depends_on) & antigravity_work
            else item
            for item in safe.assignments
        ]
    if workload_v1:
        # Normalize after structural rewrites so a generated implementation lane
        # cannot retain a pre-rewrite short/simple classification.
        safe.assignments = [
            _trusted_v1_assignment(item) for item in safe.assignments
        ]
    if (
        safe.workload_policy_version == "v1"
        and settings.workload_policy_enabled
        and safe.prestage is not None
    ):
        try:
            safe.prestage = _canonical_prestage_record(safe.prestage)
        except (ValueError, WorkloadPolicyError):
            # Keep the rejected proposal intact so policy reports the precise
            # structural failure instead of turning it into an internal error.
            pass
    safe.workload_enforcement = _workload_enforcement_record(safe, settings)
    return safe


def synchronize_trusted_provider_selection(
    plan: DirectorPlan,
    selected_providers: dict[int, ProviderName],
    settings: Settings,
) -> DirectorPlan:
    """Bind deterministic runtime provider choices into the exact plan packet.

    Selection happens outside model output, using authenticated runtime/config
    evidence. This function owns only the immutable plan rewrite: every sequence
    must be accounted for, stale provider model labels are removed, trusted size
    rules are re-applied, and the pre-stage producer/reviewer/digest are rebuilt.
    """

    safe = deepcopy(plan)
    expected_sequences = {item.sequence for item in safe.assignments}
    if set(selected_providers) != expected_sequences:
        raise WorkloadPolicyError(
            "trusted provider selection must account for every plan assignment"
        )
    if any(
        not isinstance(sequence, int) or not isinstance(provider, ProviderName)
        for sequence, provider in selected_providers.items()
    ):
        raise WorkloadPolicyError(
            "trusted provider selection requires typed sequence/provider bindings"
        )

    rewritten: list[Assignment] = []
    for item in safe.assignments:
        selected = selected_providers[item.sequence]
        if selected in {ProviderName.OWNER, ProviderName.OPENAI}:
            raise WorkloadPolicyError(
                "ordinary trusted routing cannot select owner or OpenAI Director"
            )
        updates: dict[str, object] = {"agent": selected}
        if selected != item.agent:
            updates["model_label"] = None
        rewritten.append(_trusted_v1_assignment(item.model_copy(update=updates)))
    safe.assignments = rewritten

    if safe.prestage is not None:
        ordered = sorted(safe.assignments, key=lambda item: item.sequence)
        reviews = [item for item in ordered if _work_kind(item) == WorkKind.REVIEW]
        reviewer = reviews[0] if reviews else None
        by_sequence = {item.sequence: item for item in ordered}
        producer = None
        if reviewer is not None:
            producer = next(
                (
                    by_sequence[sequence]
                    for sequence in reviewer.depends_on
                    if sequence in by_sequence
                    and _work_kind(by_sequence[sequence]) != WorkKind.REVIEW
                ),
                None,
            )
        if producer is None:
            producer = next(
                (item for item in ordered if _work_kind(item) != WorkKind.REVIEW),
                None,
            )
        if producer is None or reviewer is None:
            raise WorkloadPolicyError(
                "trusted provider selection requires a producer and independent reviewer"
            )
        safe.prestage = _canonical_prestage_record(
            safe.prestage.model_copy(
                update={
                    "producer": producer.agent,
                    "independent_reviewer": reviewer.agent,
                    "digest": None,
                }
            )
        )
    safe.workload_enforcement = _workload_enforcement_record(safe, settings)
    return safe


def preview_plan(text: str, settings: Settings) -> DirectorPlan:
    lowered = text.lower()
    if any(word in lowered for word in ("research", "sources", "current", "verify facts")):
        provider = ProviderName.PERPLEXITY
    elif _ANTIGRAVITY_SCOPE.search(text):
        provider = ProviderName.GEMINI
    elif any(word in lowered for word in ("cursor", "background agent", "independent coding pass", "second coding pass")):
        provider = ProviderName.CURSOR
    else:
        provider = ProviderName.CLAUDE

    project = "unspecified"
    match = re.search(r"(?im)^\s*project\s*:\s*(.+)$", text)
    if match:
        project = match.group(1).strip()[:255]

    assignment_kind = AssignmentKind.GENERAL
    public_research_query: str | None = None
    workload_size = WorkSize.SHORT
    if provider == ProviderName.GEMINI:
        if re.search(r"\bboilerplate\b", text, re.I):
            assignment_kind = AssignmentKind.BOILERPLATE
        elif re.search(r"\bscaffold(?:ing)?\b", text, re.I):
            assignment_kind = AssignmentKind.SCAFFOLDING
        elif re.search(r"\b(low[- ]level|mechanical|fixture generation)\b", text, re.I):
            assignment_kind = AssignmentKind.LOW_LEVEL_IMPLEMENTATION
        else:
            assignment_kind = AssignmentKind.ABSTRACT_IDEATION
    elif provider == ProviderName.PERPLEXITY:
        assignment_kind = AssignmentKind.RESEARCH
        # This is deliberately generic: dry-run preview must never turn raw intake
        # into a public query, especially when the intake mentions private context.
        public_research_query = "public sources for the owner-defined research topic"
    elif _IMPLEMENTATION_SCOPE.search(text):
        assignment_kind = AssignmentKind.IMPLEMENTATION
        workload_size = (
            WorkSize.MEDIUM
            if provider == ProviderName.CLAUDE
            else WorkSize.SHORT
        )
    preview_repository = (
        settings.repository_allowlist[0]
        if settings.repository_allowlist
        else "dry-run/preview"
    )
    pinned_commit = settings.langgraph_primary_pinned_commit_sha.strip().lower()
    pinned_tree = settings.langgraph_primary_pinned_tree_sha.strip().lower()
    pinned_mode = bool(pinned_commit and pinned_tree)
    plan_starting_ref = pinned_commit if pinned_mode else "main"
    plan_worktree = (
        str(Path(settings.repository_snapshot_root).resolve())
        if pinned_mode
        else "DRY-RUN-NO-WORKTREE"
    )
    assignment_repository = (
        None
        if provider in {ProviderName.GEMINI, ProviderName.PERPLEXITY}
        or not settings.repository_allowlist
        else preview_repository
    )
    assignment = Assignment(
        sequence=1,
        agent=provider,
        kind=assignment_kind,
        workload_size=workload_size,
        effort_points=5 if workload_size == WorkSize.MEDIUM else 2,
        model_label=(
            FABLE_MODEL_FLOOR
            if provider == ProviderName.CLAUDE
            and workload_size == WorkSize.MEDIUM
            else None
        ),
        task=text.strip(),
        acceptance=["Owner-defined outcome is demonstrably satisfied", "Evidence and unresolved risks are reported"],
        public_research_query=public_research_query,
        repository=assignment_repository,
        starting_ref=plan_starting_ref,
        requires_approval=False,
        estimated_cost_usd=0,
    )
    assignments = [assignment]
    if provider == ProviderName.GEMINI:
        assignments.append(
            Assignment(
                sequence=2,
                agent=ProviderName.CLAUDE,
                kind=AssignmentKind.IMPLEMENTATION,
                workload_size=WorkSize.SHORT,
                effort_points=2,
                task=ANTIGRAVITY_CONSUMER_TASK,
                acceptance=list(ANTIGRAVITY_CONSUMER_ACCEPTANCE),
                repository=preview_repository,
                starting_ref=plan_starting_ref,
                depends_on=[1],
                estimated_cost_usd=0,
            )
        )
    producer = ProviderName.CLAUDE if provider == ProviderName.GEMINI else provider
    reviewer = (
        ProviderName.CURSOR
        if producer == ProviderName.CLAUDE
        else ProviderName.CLAUDE
    )
    reviewed_sequence = assignments[-1].sequence
    assignments.append(
        Assignment(
            sequence=reviewed_sequence + 1,
            agent=reviewer,
            kind=AssignmentKind.INDEPENDENT_REVIEW,
            workload_size=WorkSize.SHORT,
            effort_points=2,
            task="Independently review the bounded dry-run plan and required evidence",
            acceptance=["Report contradictions, missing evidence, and unresolved risk"],
            repository=preview_repository,
            starting_ref=plan_starting_ref,
            depends_on=[reviewed_sequence],
            estimated_cost_usd=0,
        )
    )
    final_acceptance = [
        "All assignment checks pass",
        "No approval gate was bypassed",
    ]
    plan = DirectorPlan(
        project=project,
        normalized_outcome=text.strip(),
        risk_level=RiskLevel.LOW,
        approval_required=False,
        approval_reasons=[],
        max_hops=min(max(3, len(assignments)), settings.max_agent_hops),
        max_revisions=min(1, settings.max_revisions),
        assignments=assignments,
        final_acceptance=final_acceptance,
        owner_question=None,
        workload_policy_version="v1",
        workload_target_variance_reason=(
            "Dry-run previews do not manufacture assignments to imitate rolling targets"
        ),
        prestage=PlanPrestageRecord(
            packet_id="dry-run-preview",
            goal=text.strip(),
            terminal_condition="\n".join(final_acceptance),
            repository=preview_repository,
            worktree=plan_worktree,
            branch=plan_starting_ref,
            base_sha=pinned_commit if pinned_mode else "0" * 40,
            candidate_sha=None,
            producer=producer,
            independent_reviewer=reviewer,
            owned_paths=("dry-run/no-files",),
            prohibited_paths=("all external writes",),
            required_tests=("Dry-run preview only; no command executed",),
            required_security_evidence=(
                "Paid provider dispatch and external writes remain disabled",
            ),
            # Budget the effort the *trusted* normalization will assign, not the
            # points these draft assignments happen to carry. `enforce_plan`
            # raises every implementation lane to its minimum size below, and the
            # pre-stage budget gate then compares against those raised points.
            budget_units=sum(
                _trusted_v1_assignment(item).effort_points for item in assignments
            ),
            timeout_seconds=max(1, settings.provider_timeout_seconds),
            retry_limit=3,
            merge_eligibility_conditions=("A fresh exact-SHA plan is required",),
            stopping_conditions=("Stop before any provider dispatch",),
        ),
    )
    return enforce_plan(plan, text, settings)
# [[Codex | GPT-5.6 | 2026-08-24 | 13:02 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 13:34 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 14:01 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 14:47 UTC | END]]
# [[Cursor | Cursor Grok 4.6 | 2026-08-24 | 19:32 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-24 | 23:46 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 22:11 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 00:25 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 04:05 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 05:23 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 06:29 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 06:59 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 07:18 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 02:18 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 04:15 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:18 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:16 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:39 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:17 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:23 UTC | END]]
