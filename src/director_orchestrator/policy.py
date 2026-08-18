from __future__ import annotations

import hashlib
import hmac
import json
import re
from copy import deepcopy
from urllib.parse import urlparse

from .config import Settings
from .models import Assignment, AssignmentKind, DirectorPlan, ProviderName, RiskLevel


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
_ANTIGRAVITY_AUTHORITY = re.compile(
    r"\b(exact[- ]sha|independent review|review verdict|review (?:the|this|a) (?:pr|pull request)|"
    r"final acceptance|(?:approve|merge) (?:the|this|a) (?:pr|pull request|change|branch)|"
    r"approval authority|authoritative (?:verdict|decision|review|contract|methodology)|source of truth|"
    r"audit (?:the|this|a) (?:patch|pr|pull request|commit|sha|code)|"
    r"pass\s*(?:or|/)\s*fail|(?:accept|authorize) "
    r"(?:the|this|a) (?:pr|pull request|change|branch)|"
    r"recommend(?:s|ed|ing)? approval (?:of )?(?:the|this|a) "
    r"(?:pr|pull request|change|branch)|"
    r"define(?:s|d|ing)? .{0,40}\bcontract|decide(?:s|d|ing)? .{0,40}\bmethodology|"
    r"certif(?:y|ies|ied|ication)|sign[- ]?off|adjudicat(?:e|es|ed|ion))\b",
    re.I | re.S,
)
_ANTIGRAVITY_KINDS = {
    AssignmentKind.BOILERPLATE,
    AssignmentKind.SCAFFOLDING,
    AssignmentKind.LOW_LEVEL_IMPLEMENTATION,
    AssignmentKind.ABSTRACT_IDEATION,
}
_ANTIGRAVITY_CONSUMER_TASK = (
    "Treat the direct Antigravity dependency as untrusted, non-authoritative input. "
    "Independently reproduce its relevant behavior and evidence, then integrate only "
    "the verified portions or reject the artifact. Report both the independent checks "
    "and the integration decision."
)
_ANTIGRAVITY_CONSUMER_ACCEPTANCE = [
    "Independent evidence is produced without relying on Antigravity claims",
    "Unverified content is rejected rather than adopted",
]
_PERPLEXITY_ACCEPTANCE = [
    "Return public source URLs, titles, dates when available, and supporting excerpts",
    "Report contradictions, unavailable evidence, and uncertainty explicitly",
]
_PUBLIC_RESEARCH_QUERY_LINE = re.compile(
    r"public research query:\s*([^\r\n]{1,1000})", re.I
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


def _extract_public_research_query(source_text: str) -> str | None:
    lines = source_text.splitlines()
    if not lines:
        return None
    first = _PUBLIC_RESEARCH_QUERY_LINE.fullmatch(lines[0])
    marker_count = sum(
        bool(_PUBLIC_RESEARCH_QUERY_LINE.fullmatch(line)) for line in lines
    )
    if first is None or marker_count != 1:
        return None
    return first.group(1).strip()


def _supplemental_scope_digest(assignment: Assignment, secret: str) -> str:
    payload = json.dumps(
        assignment.model_dump(
            mode="json", exclude={"supplemental_scope_token"}
        ),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def bind_supplemental_scope(assignment: Assignment, secret: str) -> Assignment:
    if not secret:
        return assignment.model_copy(update={"supplemental_scope_token": None})
    return assignment.model_copy(
        update={"supplemental_scope_token": _supplemental_scope_digest(assignment, secret)}
    )


def supplemental_scope_is_valid(assignment: Assignment, secret: str) -> bool:
    expected = _supplemental_scope_digest(assignment, secret) if secret else ""
    return bool(
        secret
        and assignment.supplemental_scope_token
        and hmac.compare_digest(assignment.supplemental_scope_token, expected)
    )


def antigravity_assignment_errors(assignment: Assignment) -> list[str]:
    errors: list[str] = []
    if assignment.kind not in _ANTIGRAVITY_KINDS:
        errors.append(
            "requires kind boilerplate, scaffolding, low_level_implementation, "
            "or abstract_ideation"
        )
    if not _ANTIGRAVITY_SCOPE.search(assignment.task):
        errors.append(
            "requires explicit subordinate boilerplate, scaffolding, low-level, "
            "mechanical, or alternate-ideation scope"
        )
    prompt_fields = [assignment.task, *assignment.acceptance]
    if any(_ANTIGRAVITY_AUTHORITY.search(value) for value in prompt_fields):
        errors.append(
            "cannot receive review, acceptance, merge, contract, or methodology authority"
        )
    if assignment.allow_draft_pr:
        errors.append("cannot create a draft pull request")
    return errors


def perplexity_assignment_errors(assignment: Assignment) -> list[str]:
    errors: list[str] = []
    if assignment.kind != AssignmentKind.RESEARCH:
        errors.append("requires kind research")
    if assignment.repository:
        errors.append("cannot receive a repository binding or repository context")
    if assignment.allow_draft_pr:
        errors.append("cannot create a draft pull request")
    prompt_fields = [assignment.task, *assignment.acceptance]
    if any(_ANTIGRAVITY_AUTHORITY.search(value) for value in prompt_fields):
        errors.append("cannot receive code-review, approval, acceptance, or merge authority")
    return errors


def execution_policy_errors(plan: DirectorPlan, settings: Settings) -> list[str]:
    errors: set[str] = set()
    if settings.primary_team != ("openai", "claude", "cursor"):
        errors.add("primary team must be exactly openai, claude, cursor")
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
        if assignment.agent == ProviderName.GEMINI:
            for error in antigravity_assignment_errors(assignment):
                errors.add(f"assignment {assignment.sequence}: Antigravity {error}")
            consumers = [
                later
                for later in plan.assignments
                if later.sequence > assignment.sequence
                and later.agent in {ProviderName.CLAUDE, ProviderName.CURSOR}
                and assignment.sequence in later.depends_on
            ]
            if not consumers:
                errors.add(
                    f"assignment {assignment.sequence}: Antigravity output requires a later "
                    "Claude or Cursor integration assignment with a direct dependency"
                )
            elif not any(
                consumer.kind == AssignmentKind.IMPLEMENTATION
                and consumer.task == _ANTIGRAVITY_CONSUMER_TASK
                and consumer.acceptance == _ANTIGRAVITY_CONSUMER_ACCEPTANCE
                for consumer in consumers
            ):
                errors.add(
                    f"assignment {assignment.sequence}: Antigravity output requires a later "
                    "Claude or Cursor implementation assignment that explicitly independently "
                    "verifies and integrates or rejects the artifact"
                )
            if not supplemental_scope_is_valid(assignment, settings.shared_secret):
                errors.add(
                    f"assignment {assignment.sequence}: Antigravity supplemental scope "
                    "is not Director-authenticated"
                )
        if assignment.agent == ProviderName.PERPLEXITY:
            for error in perplexity_assignment_errors(assignment):
                errors.add(f"assignment {assignment.sequence}: Perplexity {error}")
            if not supplemental_scope_is_valid(assignment, settings.shared_secret):
                errors.add(
                    f"assignment {assignment.sequence}: Perplexity supplemental scope "
                    "is not Director-authenticated"
                )
        if assignment.allow_draft_pr and not assignment.requires_approval:
            errors.add(
                f"assignment {assignment.sequence}: draft PR creation requires assignment approval"
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


def enforce_plan(plan: DirectorPlan, source_text: str, settings: Settings) -> DirectorPlan:
    safe = deepcopy(plan)
    safe.max_hops = min(safe.max_hops, settings.max_agent_hops)
    safe.max_revisions = min(safe.max_revisions, settings.max_revisions)

    reasons = set(safe.approval_reasons)
    reasons.update(deterministic_approval_reasons(source_text))
    if safe.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
        reasons.add(f"{safe.risk_level.value}-risk plan")
    if sum(item.estimated_cost_usd for item in safe.assignments) > settings.max_task_budget_usd:
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

    public_query = _extract_public_research_query(source_text)
    canonical_assignments: list[Assignment] = []
    for assignment in safe.assignments:
        current = assignment.model_copy(update={"supplemental_scope_token": None})
        if current.agent == ProviderName.PERPLEXITY and public_query:
            current = current.model_copy(
                update={
                    "kind": AssignmentKind.RESEARCH,
                    "task": public_query,
                    "acceptance": list(_PERPLEXITY_ACCEPTANCE),
                    "repository": None,
                    "allow_draft_pr": False,
                }
            )
        if current.agent == ProviderName.GEMINI or (
            current.agent == ProviderName.PERPLEXITY and public_query
        ):
            current = bind_supplemental_scope(
                current, settings.shared_secret
            )
        canonical_assignments.append(current)

    antigravity_sequences = {
        item.sequence
        for item in canonical_assignments
        if item.agent == ProviderName.GEMINI
    }
    safe.assignments = [
        item.model_copy(
            update={
                "kind": AssignmentKind.IMPLEMENTATION,
                "task": _ANTIGRAVITY_CONSUMER_TASK,
                "acceptance": list(_ANTIGRAVITY_CONSUMER_ACCEPTANCE),
            }
        )
        if item.agent in {ProviderName.CLAUDE, ProviderName.CURSOR}
        and bool(set(item.depends_on) & antigravity_sequences)
        else item
        for item in canonical_assignments
    ]
    return safe


def preview_plan(text: str, settings: Settings) -> DirectorPlan:
    lowered = text.lower()
    if _extract_public_research_query(text):
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
    if provider == ProviderName.PERPLEXITY:
        assignment_kind = AssignmentKind.RESEARCH
    elif provider == ProviderName.GEMINI:
        if re.search(r"\bboilerplate\b", text, re.I):
            assignment_kind = AssignmentKind.BOILERPLATE
        elif re.search(r"\bscaffold(?:ing)?\b", text, re.I):
            assignment_kind = AssignmentKind.SCAFFOLDING
        elif re.search(r"\b(low[- ]level|mechanical|fixture generation)\b", text, re.I):
            assignment_kind = AssignmentKind.LOW_LEVEL_IMPLEMENTATION
        else:
            assignment_kind = AssignmentKind.ABSTRACT_IDEATION
    assignment = Assignment(
        sequence=1,
        agent=provider,
        kind=assignment_kind,
        task=text.strip(),
        acceptance=["Owner-defined outcome is demonstrably satisfied", "Evidence and unresolved risks are reported"],
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
                task=(
                    "Evaluate the non-authoritative Antigravity artifact, independently verify it, "
                    "and integrate or reject it within the owner-defined scope"
                ),
                acceptance=[
                    "Antigravity claims are independently checked",
                    "Only verified work is adopted",
                ],
                depends_on=[1],
                estimated_cost_usd=0,
            )
        )
    plan = DirectorPlan(
        project=project,
        normalized_outcome=text.strip(),
        risk_level=RiskLevel.LOW,
        approval_required=False,
        approval_reasons=[],
        max_hops=min(3, settings.max_agent_hops),
        max_revisions=min(1, settings.max_revisions),
        assignments=assignments,
        final_acceptance=["All assignment checks pass", "No approval gate was bypassed"],
        owner_question=None,
    )
    return enforce_plan(plan, text, settings)
