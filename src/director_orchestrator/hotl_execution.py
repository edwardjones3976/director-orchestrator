# [[Claude Code | Claude Opus 5 | 2026-09-09 | 01:51 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 22:05 UTC | BEGIN]]
"""Evaluate whether a proposed action may run without returning to the owner.

The predicate is H(a) = A and G and B and V and I and S: authorization, gates,
bounds, verification, independence, safe stopping. Every term must hold; there
is no weighting and no majority.

Two properties matter more than the conjunction itself.

The tier check runs before any term is evaluated and fails closed. Tier 1 is the
closed three-class list in docs/human-out-of-the-loop-operation.md; anything not
plainly one of those is tier 0 and can never be autonomous, however many terms
pass. A grant naming an unlisted class does not create one.

A hold names the blocking term rather than asking for permission. Missing
evidence routes to verification, a temporary failure routes to an authorized
recovery, and only a blocker needing authority outside the standing grant
reaches the owner. That is the difference between removing the owner from
routine execution and merely renaming the interruption.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


# Closed, and ordered as the document orders it. Membership is literal.
TIER_ONE_CLASSES: frozenset[str] = frozenset(
    {
        "eligible_merge_non_protected_branch",
        "mark_pull_request_ready_for_review",
        "post_to_own_designated_slack_channel",
    }
)

TERMS: tuple[str, ...] = (
    "authorization",
    "gates",
    "bounds",
    "verification",
    "independence",
    "safe_stopping",
)

# A term the document routes to the owner rather than to a recovery. The rule is
# the document's own: "only a blocker needing authority outside the standing
# grant reaches the owner." An unmet `authorization` *is* that blocker -- it says
# no standing grant covers this action -- so no other term's recovery may carry
# the decision past it. Without this, evidence with authorization unmet and any
# recoverable second term returned RECOVER instead of HOLD.
NON_RECOVERABLE_TERMS: frozenset[str] = frozenset({"authorization"})


class Outcome(StrEnum):
    EXECUTE = "execute"
    RECOVER = "recover"
    HOLD = "hold"


@dataclass(frozen=True, slots=True)
class ProposedAction:
    action_class: str
    repository: str
    environment: str
    candidate_sha: str
    acting_identity: str


@dataclass(frozen=True, slots=True)
class TermEvidence:
    """What the caller observed for one term. `reason` is required when unmet."""

    satisfied: bool
    reason: str | None = None
    # A recovery this term itself authorises, if the failure is recoverable.
    recovery: str | None = None

    def __post_init__(self) -> None:
        if not self.satisfied and not self.reason:
            raise ValueError("an unsatisfied term must state its blocker")
        if self.satisfied and self.recovery:
            raise ValueError("a satisfied term cannot carry a recovery")


@dataclass(frozen=True, slots=True)
class Decision:
    outcome: Outcome
    action: ProposedAction
    tier: int
    blockers: tuple[str, ...] = ()
    recovery: str | None = None
    evaluated_at: datetime | None = None
    terms: dict[str, bool] = field(default_factory=dict)

    @property
    def autonomous(self) -> bool:
        return self.outcome is Outcome.EXECUTE


def classify(action: ProposedAction) -> int:
    """Return 1 only for a literal tier-1 class. Everything else is tier 0."""

    return 1 if action.action_class in TIER_ONE_CLASSES else 0


def decide(
    action: ProposedAction,
    evidence: dict[str, TermEvidence],
    *,
    now: datetime | None = None,
) -> Decision:
    """Decide whether `action` runs autonomously, recovers, or holds."""

    missing = [term for term in TERMS if term not in evidence]
    if missing:
        raise ValueError(f"every term must be evaluated; missing: {', '.join(missing)}")

    # A caller that supplies a seventh term believes it is being evaluated. It
    # was not: the loops below walk TERMS, so an unmet `audit` was dropped on
    # the floor and EXECUTE still returned. Refuse rather than silently ignore
    # -- an unrecognised term is more likely a gate this predicate does not yet
    # implement than a typo, and dropping it is exactly how bar items leave.
    unknown = sorted(term for term in evidence if term not in TERMS)
    if unknown:
        raise ValueError(
            "H(a) evaluates exactly these terms and cannot silently drop another; "
            f"unrecognised: {', '.join(unknown)}"
        )

    tier = classify(action)
    if tier == 0:
        # Ahead of the conjunction on purpose: a tier-0 act is not delegable
        # under any configuration, so no combination of satisfied terms may
        # produce an autonomous execution.
        return Decision(
            outcome=Outcome.HOLD,
            action=action,
            tier=0,
            blockers=(f"{action.action_class} is not a tier 1 class",),
            evaluated_at=now,
            terms={term: evidence[term].satisfied for term in TERMS},
        )

    states = {term: evidence[term].satisfied for term in TERMS}
    unmet = tuple(term for term in TERMS if not states[term])
    if not unmet:
        return Decision(Outcome.EXECUTE, action, 1, (), None, now, states)

    # A recoverable blocker routes to its recovery rather than to the owner.
    # Only the first is offered: recoveries are re-evaluated from scratch, so
    # chaining them here would skip a fresh reading of every term.
    #
    # A non-recoverable term unmet anywhere in the set stops that: it needs
    # authority outside the standing grant, and no sibling term's recovery
    # supplies that authority.
    if any(term in NON_RECOVERABLE_TERMS for term in unmet):
        return Decision(
            outcome=Outcome.HOLD,
            action=action,
            tier=1,
            blockers=tuple(f"{t}: {evidence[t].reason}" for t in unmet),
            evaluated_at=now,
            terms=states,
        )
    for term in unmet:
        recovery = evidence[term].recovery
        if recovery:
            return Decision(
                outcome=Outcome.RECOVER,
                action=action,
                tier=1,
                blockers=tuple(f"{t}: {evidence[t].reason}" for t in unmet),
                recovery=recovery,
                evaluated_at=now,
                terms=states,
            )

    return Decision(
        outcome=Outcome.HOLD,
        action=action,
        tier=1,
        blockers=tuple(f"{t}: {evidence[t].reason}" for t in unmet),
        evaluated_at=now,
        terms=states,
    )


@dataclass(frozen=True, slots=True)
class CompletionRate:
    """Autonomous completion over a fixed denominator.

    Eligibility and deadline are fixed before execution, so a workflow that
    failed or blocked cannot leave the denominator by being reclassified after
    the fact. Only workflows whose deadline has passed are counted at all.
    """

    completed_without_intervention: int
    eligible_past_deadline: int

    def __post_init__(self) -> None:
        if self.eligible_past_deadline < 0 or self.completed_without_intervention < 0:
            raise ValueError("counts cannot be negative")
        if self.completed_without_intervention > self.eligible_past_deadline:
            raise ValueError("completions cannot exceed the eligible population")

    @property
    def percent(self) -> float | None:
        if self.eligible_past_deadline == 0:
            return None
        return 100.0 * self.completed_without_intervention / self.eligible_past_deadline
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 22:05 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 01:51 UTC | END]]
