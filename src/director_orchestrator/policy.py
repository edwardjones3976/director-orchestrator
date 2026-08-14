from __future__ import annotations

import re
from copy import deepcopy
from urllib.parse import urlparse

from .config import Settings
from .models import Assignment, DirectorPlan, ProviderName, RiskLevel


_GATES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("production deployment or public publishing", re.compile(r"\b(deploy|release|publish|go live|production|prod)\b", re.I)),
    ("merge or protected-branch change", re.compile(r"\b(merge|protected branch|push (?:to )?(?:main|master)|directly to main)\b", re.I)),
    ("destructive action", re.compile(r"\b(delete|destroy|drop database|truncate|purge|wipe|remove account|rewrite history|force push)\b", re.I)),
    ("purchase or paid upgrade", re.compile(r"\b(buy|purchase|subscribe|upgrade plan|charge|payment|spend)\b", re.I)),
    ("external representation", re.compile(r"\b(send|email|message|post|reply|submit)\b.{0,50}\b(customer|client|public|vendor|agency|employer|recipient)\b", re.I | re.S)),
    ("sensitive data", re.compile(r"\b(ssn|social security|passport|medical|health|financial|bank|credit card|identity|genealogy evidence|dna|authentication data)\b", re.I)),
    ("credential or permission change", re.compile(r"\b(api key|token|password|secret|credential|oauth|permission|access scope|administrator|admin access)\b", re.I)),
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


def execution_policy_errors(plan: DirectorPlan, settings: Settings) -> list[str]:
    errors: set[str] = set()
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
        if assignment.agent == ProviderName.CURSOR and not assignment.repository:
            errors.add(f"assignment {assignment.sequence}: Cursor requires an explicit repository")
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
    return safe


def preview_plan(text: str, settings: Settings) -> DirectorPlan:
    lowered = text.lower()
    if any(word in lowered for word in ("research", "sources", "current", "verify facts")):
        provider = ProviderName.PERPLEXITY
    elif any(word in lowered for word in ("prototype", "browser", "visual", "alternate implementation")):
        provider = ProviderName.GEMINI
    elif any(word in lowered for word in ("cursor", "background agent", "independent coding pass", "second coding pass")):
        provider = ProviderName.CURSOR
    else:
        provider = ProviderName.CLAUDE

    project = "unspecified"
    match = re.search(r"(?im)^\s*project\s*:\s*(.+)$", text)
    if match:
        project = match.group(1).strip()[:255]

    assignment = Assignment(
        sequence=1,
        agent=provider,
        task=text.strip(),
        acceptance=["Owner-defined outcome is demonstrably satisfied", "Evidence and unresolved risks are reported"],
        requires_approval=False,
        estimated_cost_usd=0,
    )
    plan = DirectorPlan(
        project=project,
        normalized_outcome=text.strip(),
        risk_level=RiskLevel.LOW,
        approval_required=False,
        approval_reasons=[],
        max_hops=min(3, settings.max_agent_hops),
        max_revisions=min(1, settings.max_revisions),
        assignments=[assignment],
        final_acceptance=["All assignment checks pass", "No approval gate was bypassed"],
        owner_question=None,
    )
    return enforce_plan(plan, text, settings)
