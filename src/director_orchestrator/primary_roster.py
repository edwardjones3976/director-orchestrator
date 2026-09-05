# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | BEGIN]]
"""Trusted capability selection over actual primary admission history.

Available transports do not imply task requirements. Selection is frozen into the
plan before approval; admission still rechecks the ledger atomically at dispatch.
"""
from collections import Counter
from dataclasses import dataclass
from typing import Mapping, Sequence

from .primary_workload import TARGETS, admission_reason

PRIMARY_TEAM = frozenset({"openai", "claude", "cursor", "grok"})
IMPLEMENTERS = ("claude", "cursor", "grok")
KNOWN_LANES = PRIMARY_TEAM | {"antigravity", "perplexity", "firecrawl", "github-copilot", "microsoft-365-copilot"}

class PrimaryRosterHold(ValueError):
    """A pre-dispatch capability or allocation hold; never a provider failure."""


def core_inventory_ready(available: Sequence[str]) -> bool:
    pool = set(available)
    return "openai" in pool and len(pool & PRIMARY_TEAM) >= 3


@dataclass(frozen=True)
class PrimaryRoster:
    producer: str
    reviewer: str
    research: str | None = None


def select_primary_roster(*, available: Sequence[str], history: Sequence[str],
                          required_capabilities: Sequence[str] = (),
                          public_research_query: str | None = None,
                          trusted_cost_ranks: Mapping[str, int]) -> PrimaryRoster:
    pool = set(available)
    if pool - KNOWN_LANES:
        raise PrimaryRosterHold("primary_inventory_unknown_identity")
    if not core_inventory_ready(available):
        raise PrimaryRosterHold("primary_core_capability_unavailable")
    if "enterprise_context" in required_capabilities:
        # Enterprise lanes lack a production allocation target. Never alias them
        # to another provider or grant access merely from a requested capability.
        raise PrimaryRosterHold("primary_enterprise_accounting_authority_required")
    if set(required_capabilities) - {"public_research", "enterprise_context"}:
        raise PrimaryRosterHold("primary_task_capability_unknown")
    counts = Counter(history[-19:])
    eligible = [lane for lane in IMPLEMENTERS if lane in pool and admission_reason(lane, history) is None]
    if any(type(trusted_cost_ranks.get(lane)) is not int or trusted_cost_ranks[lane] < 0 for lane in eligible):
        raise PrimaryRosterHold("primary_trusted_cost_rank_required")
    def rank(lane: str):
        prospective = (counts[lane] + 1) * 100
        tier = 0 if prospective < (TARGETS[lane] - 3) * 20 else 2 if prospective > (TARGETS[lane] + 3) * 20 else 1
        return tier, trusted_cost_ranks[lane], lane
    eligible.sort(key=rank)
    if len(eligible) < 2:
        raise PrimaryRosterHold("primary_independent_review_allocation_unavailable")
    research = None
    if "public_research" in required_capabilities:
        if not public_research_query:
            raise PrimaryRosterHold("primary_public_research_scope_required")
        if "perplexity" not in pool or admission_reason("perplexity", history) is not None:
            raise PrimaryRosterHold("primary_public_research_unavailable")
        research = "perplexity"
    return PrimaryRoster(eligible[0], eligible[1], research)
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | END]]
