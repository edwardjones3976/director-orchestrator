# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:27 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 07:05 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:25 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 16:52 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 02:32 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 01:51 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 22:05 UTC | BEGIN]]
"""Evaluate whether a proposed action may run without returning to the owner.

The predicate is H(a) = A and G and B and V and I and S and P and D:
authorization, gates, bounds, verification, independence, safe stopping, path
scope, durable audit. Every term must hold; there is no weighting and no
majority.

The six-letter form came from the owner's approved analysis and was implemented
literally. It was short by two: the governing document's evidence bar has ten
items, and items 6 (owned and prohibited paths) and 10 (audit written before
execution) mapped to no term at all, so the predicate could return EXECUTE
without either ever being read. `TERMS` below records which bar item each term
carries, because an unmapped item is the failure this had.

Two properties matter more than the conjunction itself.

The tier check runs before any term is evaluated and fails closed. Tier 1 is the
closed four-class list in docs/human-out-of-the-loop-operation.md; anything not
plainly one of those is tier 0 and can never be autonomous, however many terms
pass. A grant naming an unlisted class does not create one. The one
pre-execution class is decided by `decide_pre_execution` over its own closed
term list, never by H(a), because it is decided before any candidate or verdict
exists.

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
        "execute_small_read_only_task",
    }
)

# Every item of the document's ten-item evidence bar reaches exactly one term.
# The mapping is stated here because it is the thing that can silently rot: a
# bar item with no term is not a relaxed requirement, it is an unevaluated one,
# and the predicate returns EXECUTE without ever having looked at it.
#
#   authorization  item 9   grant live, unexpired, unrevoked, correctly scoped
#   gates          items 1, 2, 7   CI green, security evidence, no tier-0 implied
#   bounds         item 8   budget, attempt and hop ledgers within bounds
#   verification   evidence current and bound to the exact candidate
#   independence   items 3, 4, 5   one exact-SHA ACCEPT from the non-author
#                  reviewer, who also merges; author and reviewer distinct
#   safe_stopping  failure handling, cancellation and recovery defined
#   scope          item 6   only owned_paths touched, no prohibited_paths
#   audit          item 10  the audit record durably written before execution
#
# `scope` and `audit` were absent, so a caller could satisfy H(a) with a change
# reaching prohibited paths and no audit record. Found by an independent review
# of #146. Adding them adds approval requirements, which AGENTS.md rule 4
# permits; it is removing one that is forbidden.
TERMS: tuple[str, ...] = (
    "authorization",
    "gates",
    "bounds",
    "verification",
    "independence",
    "safe_stopping",
    "scope",
    "audit",
)

# Terms the document routes to the owner or to SUSPENDED, never to a recovery.
# Naming only `authorization` was too narrow. An independent review of #146
# constructed a RECOVER for each of these by pairing it with a recoverable
# `verification`, and every one is a trip condition or an explicit refusal in
# the governing document:
#
#   authorization  no standing grant covers this action at all
#   gates          "a tier-0 class is implicated at any point" -- a trip
#                  condition; this term also carries item 7, and it cannot
#                  distinguish a re-runnable red check from that implication,
#                  so it fails to the safe side
#   independence   "Absence of evidence is never consent... its silence may not
#                  be counted, inferred, or waited out" -- a silent reviewer is not a recoverable delay
#   scope          item 6, owned and prohibited paths: a change that reached a
#                  prohibited path is not retried into compliance
#   audit          "the audit ledger cannot be written" -- a trip condition
#
# What is left recoverable is the mechanical remainder: stale verification,
# exhausted bounds, undefined stopping. That is what "a temporary failure routes
# to an authorized recovery" describes.
NON_RECOVERABLE_TERMS: frozenset[str] = frozenset(
    {"authorization", "gates", "independence", "scope", "audit"}
)

# The one tier-1 class decided before any candidate or verdict exists. H(a)
# cannot evaluate it: `gates` (items 1, 2, 7) and `independence` (items 3-5)
# need a candidate head and an ACCEPT, so reusing those names here would record
# a PASS for evidence nobody read. It has its own closed term list instead, and
# items 1-5 stay with the task's own exact-SHA review and CI/security gates.
#
#   authorization          item 9   live, in-scope, unexhausted, untripped grant
#                                   and an attested owner intake
#   eligibility            item 7   policy.small_read_only_eligible() admits it
#   bounds                 item 8   hop and revision ledgers within bounds
#   verification           the exact starting commit and snapshot digest are bound
#   lane_independence      two distinct lanes from different provider families
#   safe_stopping          a positive provider timeout is configured
#   owned_paths_declared   item 6 before any change: owned paths declared and
#                          no write surface
#   audit                  item 10  PRE_AUTHORIZED written before execution
PRE_EXECUTION_CLASSES: frozenset[str] = frozenset({"execute_small_read_only_task"})
PRE_EXECUTION_TERMS: tuple[str, ...] = (
    "authorization",
    "eligibility",
    "bounds",
    "verification",
    "lane_independence",
    "safe_stopping",
    "owned_paths_declared",
    "audit",
)


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
    if action.action_class in PRE_EXECUTION_CLASSES:
        # H(a) has no candidate to read for this class; decide_pre_execution
        # alone evaluates it. Holding here only adds a requirement.
        return Decision(
            outcome=Outcome.HOLD,
            action=action,
            tier=1,
            blockers=(f"{action.action_class} is decided before execution, not by H(a)",),
            evaluated_at=now,
            terms=states,
        )
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
    # EVERY unmet term must authorise its own recovery. Offering the first
    # recovery found let one term's remedy carry every other unmet term with it:
    # an independent review of #146 paired a red `gates` with a recoverable
    # `verification` and got RECOVER, which advances a decision whose CI is
    # failing on the strength of a re-verification that has nothing to do with
    # it. The document routes "a temporary failure" to "an authorized recovery",
    # singular per failure -- a term with no recovery has nothing authorising
    # progress on it.
    if not all(evidence[term].recovery for term in unmet):
        return Decision(
            outcome=Outcome.HOLD,
            action=action,
            tier=1,
            blockers=tuple(f"{t}: {evidence[t].reason}" for t in unmet),
            evaluated_at=now,
            terms=states,
        )
    # Only the first is offered: recoveries are re-evaluated from scratch, so
    # chaining them here would skip a fresh reading of every term.
    return Decision(
        outcome=Outcome.RECOVER,
        action=action,
        tier=1,
        blockers=tuple(f"{t}: {evidence[t].reason}" for t in unmet),
        recovery=evidence[unmet[0]].recovery,
        evaluated_at=now,
        terms=states,
    )


def decide_pre_execution(
    action: ProposedAction,
    evidence: dict[str, TermEvidence],
    *,
    now: datetime | None = None,
) -> Decision:
    """Decide whether a pre-execution class may start without the owner.

    Exactly PRE_EXECUTION_TERMS, each required; no recovery, since every term
    is either a grant boundary or evidence that cannot be retried into being.
    Anything but a PRE_EXECUTION_CLASSES action holds.
    """

    if set(evidence) != set(PRE_EXECUTION_TERMS):
        raise ValueError("the pre-execution predicate evaluates exactly PRE_EXECUTION_TERMS")
    states = {term: evidence[term].satisfied for term in PRE_EXECUTION_TERMS}
    if action.action_class not in PRE_EXECUTION_CLASSES:
        return Decision(
            outcome=Outcome.HOLD,
            action=action,
            tier=classify(action),
            blockers=(f"{action.action_class} is not a pre-execution class",),
            evaluated_at=now,
            terms=states,
        )
    unmet = tuple(term for term in PRE_EXECUTION_TERMS if not states[term])
    if unmet:
        return Decision(
            outcome=Outcome.HOLD,
            action=action,
            tier=1,
            blockers=tuple(f"{t}: {evidence[t].reason}" for t in unmet),
            evaluated_at=now,
            terms=states,
        )
    return Decision(Outcome.EXECUTE, action, 1, (), None, now, states)


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
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 02:32 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 16:52 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:25 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 07:05 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:27 UTC | END]]
