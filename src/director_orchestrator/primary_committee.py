# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 05:30 UTC | BEGIN]]
"""Invited committee POSITION turns inside the LangGraph primary.

An owner-attested invitation (``SlackIntake.primary_committee_debate``) seats
each invited member as one more assignment in the ordinary primary plan. The
same packet router, admission, budget, three-attempt roster, host bridge and
persisted result artifacts serve it; nothing here dispatches. After the
terminal review the live evidence handler reads each seat's persisted result,
validates its contribution and tallies it with the charter's configured
weights. A replay reads the same immutable artifacts, so a tally never calls a
model. A seat that failed, was refused or is ambiguous never reaches the tally:
the review that depends on it does not run.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from pydantic import ValidationError

from .models import DebateContribution, DiscussionPhase, PrimaryCommitteeDebate, TaskRecord
from .team_charter import AdvisoryVote, AdvisoryVoteTally, tally_advisory_votes, team_operating_charter

_POSITION_FIELDS = ("rationale", "assumptions", "material_risks", "unknowns", "conditions_to_change")


class CommitteeContributionInvalid(ValueError):
    """A seat's persisted result cannot be counted; the debate fails closed."""

    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


def committee_seats(record: TaskRecord) -> tuple:
    """The plan's invited POSITION assignments, in sequence order."""

    if record.plan is None or record.intake.primary_committee_debate is None:
        return ()
    return tuple(sorted((item for item in record.plan.assignments
                         if item.discussion_phase is DiscussionPhase.POSITION),
                        key=lambda item: item.sequence))


def committee_position_objective(debate: PrimaryCommitteeDebate) -> str:
    return "State one bounded committee position on this owner question: " + debate.question


def committee_position_constraints(record: TaskRecord, assignment) -> list[str]:
    """What an invited seat is told, in its own work packet."""

    debate = record.intake.primary_committee_debate
    if debate is None or assignment.discussion_phase is not DiscussionPhase.POSITION:
        return []
    return [
        "Committee POSITION turn invited by the Director: state one position; never open, "
        "widen or route a debate, and take no action beyond gathering evidence.",
        "Committee vote options (choose exactly one, or abstain): "
        + json.dumps(list(debate.vote_options), ensure_ascii=False) + ".",
        "Set outcome to exactly one JSON object with position, rationale, evidence, "
        "contradictions, assumptions, material_risks, unknowns, conditions_to_change, "
        "confidence (0 to 1) and either vote_option (one listed option) or abstain: true. "
        "Any other outcome is never counted and fails the task closed.",
    ]


def committee_contribution(outcome: Any, debate: PrimaryCommitteeDebate) -> DebateContribution:
    """Validate one seat's outcome exactly as the legacy POSITION checks do."""

    try:
        document = json.loads(outcome) if isinstance(outcome, str) else None
    except ValueError as exc:
        raise CommitteeContributionInvalid("committee_contribution_not_json") from exc
    if not isinstance(document, dict):
        raise CommitteeContributionInvalid("committee_contribution_not_json")
    try:
        contribution = DebateContribution.model_validate(document)
    except ValidationError as exc:
        raise CommitteeContributionInvalid("committee_contribution_invalid") from exc
    if any(getattr(contribution, field) is None for field in _POSITION_FIELDS):
        raise CommitteeContributionInvalid("committee_position_incomplete")
    if not contribution.abstain and contribution.vote_option not in debate.vote_options:
        raise CommitteeContributionInvalid("committee_option_unconfigured")
    return contribution


def committee_tally(record: TaskRecord, results: Mapping[int, Mapping[str, Any]], *,
                    producer_lane: str | None) -> tuple[list[AdvisoryVote], AdvisoryVoteTally]:
    """Votes and tally from each seat's authenticated, persisted result packet.

    Every configured member that holds no seat is recorded unavailable: it
    casts no weight and the configured total (the denominator) is unchanged.
    """

    debate = record.intake.primary_committee_debate
    if debate is None:
        raise CommitteeContributionInvalid("committee_debate_absent")
    votes: dict[str, AdvisoryVote] = {}
    for seat in committee_seats(record):
        member = seat.primary_provider_lane
        packet = results.get(seat.sequence)
        if (packet is None or packet.get("status") != "SUCCEEDED"
                or packet.get("provider") != member or member in votes):
            raise CommitteeContributionInvalid("committee_position_unresolved")
        contribution = committee_contribution(packet.get("outcome"), debate)
        votes[member] = AdvisoryVote(
            member=member,
            disposition="abstain" if contribution.abstain else "vote",
            option=contribution.vote_option,
            rationale=contribution.rationale[:2_000],
            evidence=contribution.evidence,
            confidence=contribution.confidence,
            producer=member == producer_lane,
        )
    if set(votes) != set(debate.invited_members):
        raise CommitteeContributionInvalid("committee_position_unresolved")
    for member in sorted(team_operating_charter()["committee"]["members"]):
        votes.setdefault(member, AdvisoryVote(
            member=member, disposition="unavailable",
            rationale="Not seated in this primary debate."))
    ordered = [votes[member] for member in sorted(votes)]
    return ordered, tally_advisory_votes(ordered, material_risk=debate.material_risk)
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 05:30 UTC | END]]
