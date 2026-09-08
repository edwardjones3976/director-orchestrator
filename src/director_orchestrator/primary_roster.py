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
"""
from collections import Counter
from dataclasses import dataclass
from typing import Mapping, Sequence

from .primary_workload import admission_reason
from .allocation_profiles import allocation_targets, DEFAULT_ALLOCATION_PROFILE

PRIMARY_TEAM = frozenset({"openai", "claude", "cursor", "grok"})
IMPLEMENTERS = ("openai", "claude", "cursor", "grok", "antigravity", "github-copilot")
REVIEWERS = frozenset({"openai", "claude", "cursor", "grok"})
KNOWN_LANES = PRIMARY_TEAM | {"antigravity", "perplexity", "firecrawl", "github-copilot", "microsoft-365-copilot"}

class PrimaryRosterHold(ValueError):
    """A pre-dispatch capability or allocation hold; never a provider failure."""


def core_inventory_ready(available: Sequence[str], *, director_provider: str = "openai") -> bool:
    """Check the configured execution provider, not the acting project leader.

    The legacy floor stays OpenAI unless a caller explicitly binds the enabled
    Director adapter. A leadership change alone cannot change runtime admission.
    """
    pool = set(available)
    return director_provider in {"openai", "claude"} and director_provider in pool and len(pool & PRIMARY_TEAM) >= 3


@dataclass(frozen=True)
class PrimaryRoster:
    producer: str
    reviewer: str
    research: str | None = None


def select_primary_roster(*, available: Sequence[str], history: Sequence[str],
                          required_capabilities: Sequence[str] = (),
                          public_research_query: str | None = None,
                          trusted_cost_ranks: Mapping[str, int],
                          allocation_profile: str = DEFAULT_ALLOCATION_PROFILE,
                          team_mode: bool = False,
                          director_provider: str = "openai") -> PrimaryRoster:
    targets = allocation_targets(allocation_profile)
    pool = set(available)
    if pool - KNOWN_LANES:
        raise PrimaryRosterHold("primary_inventory_unknown_identity")
    if not core_inventory_ready(available, director_provider=director_provider):
        raise PrimaryRosterHold("primary_core_capability_unavailable")
    if "enterprise_context" in required_capabilities:
        # Enterprise targets exist, but the default graph has no scoped enterprise
        # production adapter. A capability request cannot grant tenant access.
        raise PrimaryRosterHold("primary_enterprise_scoped_adapter_required")
    if set(required_capabilities) - {"public_research", "enterprise_context", "bounded_scaffolding", "repository_assistance"}:
        raise PrimaryRosterHold("primary_task_capability_unknown")
    research = None
    if "public_research" in required_capabilities:
        if not public_research_query:
            raise PrimaryRosterHold("primary_public_research_scope_required")
        research_lanes = [lane for lane in ("perplexity", "firecrawl") if lane in pool
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
                      and (lane != "github-copilot" or "repository_assistance" in required_capabilities)
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
                     if (lane in REVIEWERS or (team_mode and lane=="antigravity"))
                     and (team_mode or producer != "antigravity" or lane in {"claude", "cursor", "grok"})]
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
