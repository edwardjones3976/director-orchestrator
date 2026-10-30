# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 03:40 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:30 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 21:47 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:51 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:48 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 05:57 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:56 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 01:02 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:20 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | BEGIN]]
"""Trusted capability selection over actual primary admission history.

Available transports do not imply task requirements. Selection is frozen into the
plan before approval; admission still rechecks the ledger atomically at dispatch.

``director_provider`` is an explicit caller-supplied capability seam. No current
production caller sets it, so the LangGraph primary floor deliberately retains
OpenAI. The default-off Claude adapter belongs to the legacy engine; enabling it
does not enroll a Claude Director in the separate primary runtime or configure
this seam. Project leadership alone changes neither runtime's admission floor.
"""
from collections import Counter
from dataclasses import dataclass
import re
from typing import Mapping, Sequence

from .primary_workload import admission_reason
from .allocation_profiles import ALLOCATION_PROFILES, allocation_targets, DEFAULT_ALLOCATION_PROFILE

PRIMARY_TEAM = frozenset({"openai", "claude", "cursor", "grok"})
# Z.ai (GLM) was enrolled by the owner on 2026-09-27 as an implementer and an
# independent reviewer of other lanes' work. It is not part of the core-inventory
# floor, and the producer/reviewer lanes must still differ.
IMPLEMENTERS = ("openai", "claude", "cursor", "grok", "antigravity", "github-copilot", "zai")
RESEARCH_LANES = ("perplexity", "firecrawl")
REVIEWERS = PRIMARY_TEAM | {"zai"}
KNOWN_LANES = PRIMARY_TEAM | {"antigravity", "perplexity", "firecrawl", "github-copilot", "zai"}

class PrimaryRosterHold(ValueError):
    """A pre-dispatch capability or allocation hold; never a provider failure."""


def core_inventory_ready(available: Sequence[str], *, director_provider: str = "openai") -> bool:
    """Check the caller-supplied execution capability; default to the OpenAI floor."""
    pool = set(available)
    return director_provider in {"openai", "claude"} and director_provider in pool and len(pool & PRIMARY_TEAM) >= 3


def lanes_requiring_cost_rank(pool: Sequence[str] | frozenset[str] | set[str]) -> frozenset[str]:
    """Lanes in ``pool`` that some admissible intake can turn into a candidate.

    ``eligible_for`` and the research selector both hold the whole roster on
    ``primary_trusted_cost_rank_required`` when a candidate has no rank. This is
    the same question asked before startup, so it reads the very tuples those
    selectors iterate -- ``IMPLEMENTERS`` and ``RESEARCH_LANES`` -- instead of
    restating them. A lane added to either tuple is covered without editing here.

    The per-intake variables are deliberately over-approximated, because they
    arrive with the task rather than the configuration:

    * Allocation profile: ``SlackIntake`` may name any profile, so a lane is
      required as soon as *some* profile gives it a non-zero target. A lane that
      no profile can admit is exempt -- ``admission_reason`` already refuses it
      with ``workload_profile_lane_ineligible`` under every profile, and a lane
      absent from the target table is refused with
      ``workload_unsupported_allocation_lane``.
    * Capability gates: the ``antigravity``/``github-copilot`` conditions inside
      ``eligible_for`` and the ``public_research`` gate on ``RESEARCH_LANES`` are
      not consulted, because any intake may request the gating capability. That
      keeps the derivation correct for a fourth gated lane without teaching this
      function about the gate.

    Callers scope ``pool`` to lanes that can actually reach a roster.
    """
    selectable = frozenset(IMPLEMENTERS) | frozenset(RESEARCH_LANES)
    return frozenset(
        lane
        for lane in frozenset(pool) & selectable
        if any(allocation_targets(profile).get(lane, 0) > 0 for profile in ALLOCATION_PROFILES)
    )


# Shared Copilot issue-scope rules: the provider-dispatch guard's
# ``dispatch_guard.copilot_issue_scope_error`` must accept and refuse exactly the
# same inputs, so a scope the roster admits is one the dispatcher admits too.
ALLOWED_COPILOT_ISSUE_KINDS = frozenset({
    "triage",
    "investigation",
    "reproduction_planning",
    "evidence",
    "drafting",
    "tracking",
})
COPILOT_REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
COPILOT_SCOPE_ALIASES = {
    "repository": ("repository", "repo"),
    "issue_number": ("issue_number", "issue_reference", "issue"),
    "packet": ("packet", "drafting_packet"),
}


@dataclass(frozen=True)
class IssueRef:
    repository: str
    issue_number: int | str | None = None
    kind: str | None = None
    packet: Mapping[str, object] | None = None


def _scope_field(scope: Mapping[str, object], field: str) -> object:
    present = [key for key in COPILOT_SCOPE_ALIASES[field] if key in scope]
    if len(present) > 1:
        raise ValueError(f"ambiguous {field}: give only one of {', '.join(present)}")
    return scope[present[0]] if present else None


def _valid_issue_number(value: object) -> bool:
    # type() rather than isinstance(): bool (and any other int subclass) is refused.
    if type(value) is int:
        return value > 0
    return isinstance(value, str) and value.isascii() and value.isdigit() and int(value) > 0


def issue_scope_error(scope: object) -> str | None:
    """None when ``scope`` is a valid typed Copilot issue scope, else the reason it is not."""
    if isinstance(scope, IssueRef):
        scope = {"repository": scope.repository, "issue_number": scope.issue_number,
                 "kind": scope.kind, "packet": scope.packet}
    if not isinstance(scope, Mapping):
        return "issue_scope must be a mapping"
    kind = scope.get("kind")
    if not isinstance(kind, str) or kind not in ALLOWED_COPILOT_ISSUE_KINDS:
        return f"kind {kind!r} not allowed; allowed kinds: {', '.join(sorted(ALLOWED_COPILOT_ISSUE_KINDS))}"
    try:
        repository = _scope_field(scope, "repository")
        number = _scope_field(scope, "issue_number")
        draft = _scope_field(scope, "packet")
    except ValueError as exc:
        return str(exc)
    if not isinstance(repository, str) or not COPILOT_REPOSITORY_PATTERN.fullmatch(repository):
        return "repository must be owner/name"
    if number is not None and not _valid_issue_number(number):
        return "issue number must be a positive integer (bool, negative and non-numeric values are refused)"
    if kind == "drafting":
        title = draft.get("title") if isinstance(draft, Mapping) else None
        if not isinstance(title, str) or not title.strip():
            return "drafting requires a packet mapping with a nonempty string title"
    elif number is None:
        return f"kind {kind!r} requires an issue number"
    return None


def is_valid_issue_ref(issue_ref: object) -> bool:
    return issue_scope_error(issue_ref) is None


@dataclass(frozen=True)
class PrimaryRoster:
    producer: str
    reviewer: str
    research: str | None = None


def select_primary_roster(*, available: Sequence[str], history: Sequence[str],
                          required_capabilities: Sequence[str] = (),
                          public_research_query: str | None = None,
                          issue_ref: IssueRef | Mapping[str, object] | None = None,
                          issue_scope: IssueRef | Mapping[str, object] | None = None,
                          trusted_cost_ranks: Mapping[str, int],
                          allocation_profile: str = DEFAULT_ALLOCATION_PROFILE,
                          team_mode: bool = False,
                          director_provider: str = "openai") -> PrimaryRoster:
    targets = allocation_targets(allocation_profile)
    pool = set(available)
    active_issue_ref = issue_ref if issue_ref is not None else issue_scope
    if pool - KNOWN_LANES:
        raise PrimaryRosterHold("primary_inventory_unknown_identity")
    if not core_inventory_ready(available, director_provider=director_provider):
        raise PrimaryRosterHold("primary_core_capability_unavailable")
    if "enterprise_context" in required_capabilities:
        # Enterprise targets exist, but the default graph has no scoped enterprise
        # production adapter. A capability request cannot grant tenant access.
        raise PrimaryRosterHold("primary_enterprise_scoped_adapter_required")
    allowed_caps = {
        "public_research",
        "enterprise_context",
        "bounded_scaffolding",
        "repository_assistance",
        "github_issue_work",
    }
    if set(required_capabilities) - allowed_caps:
        raise PrimaryRosterHold("primary_task_capability_unknown")
    has_issue_cap = "github_issue_work" in required_capabilities
    if has_issue_cap:
        if (issue_ref is not None and issue_scope is not None) or not is_valid_issue_ref(active_issue_ref):
            raise PrimaryRosterHold("primary_issue_scope_required")
    research = None
    if "public_research" in required_capabilities:
        if not public_research_query:
            raise PrimaryRosterHold("primary_public_research_scope_required")
        research_lanes = [lane for lane in RESEARCH_LANES if lane in pool
                          and admission_reason(lane, history, allocation_profile=allocation_profile) is None]
        if not research_lanes:
            raise PrimaryRosterHold("primary_public_research_unavailable")
        if any(type(trusted_cost_ranks.get(lane)) is not int or trusted_cost_ranks[lane] < 0 for lane in research_lanes):
            raise PrimaryRosterHold("primary_trusted_cost_rank_required")
        research = min(research_lanes, key=lambda lane: (trusted_cost_ranks[lane], lane))
    # Planning projects the actual dependency order only; it writes no admissions.
    projected = tuple(history[-19:]) + ((research,) if research else ())
    def eligible_for(prior: Sequence[str], *, exclude: str | None = None):
        candidates = [lane for lane in IMPLEMENTERS if lane != exclude and lane in pool
                      and (lane != "antigravity" or team_mode or "bounded_scaffolding" in required_capabilities)
                      and (lane != "github-copilot" or has_issue_cap)
                      and admission_reason(lane, prior, allocation_profile=allocation_profile) is None]
        if any(type(trusted_cost_ranks.get(lane)) is not int or trusted_cost_ranks[lane] < 0
               for lane in candidates):
            raise PrimaryRosterHold("primary_trusted_cost_rank_required")
        counts = Counter(prior[-19:])
        def rank(lane: str):
            prospective = (counts[lane] + 1) * 100
            tier = 0 if prospective < (targets[lane] - 3) * 20 else 2 if prospective > (targets[lane] + 3) * 20 else 1
            return tier, trusted_cost_ranks[lane], lane
        return sorted(candidates, key=rank)
    for producer in eligible_for(projected):
        reviewers = [lane for lane in eligible_for((*projected[-19:], producer), exclude=producer)
                     if (lane in REVIEWERS or (team_mode and lane == "antigravity"))
                     and (team_mode or producer != "antigravity" or lane in PRIMARY_TEAM)]
        if reviewers:
            return PrimaryRoster(producer, reviewers[0], research)
    raise PrimaryRosterHold("primary_independent_review_allocation_unavailable")
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:20 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 01:02 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:56 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 05:57 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:48 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:51 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 21:47 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:25 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:30 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 03:40 UTC | END]]
