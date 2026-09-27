# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 06:40 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 05:30 UTC | BEGIN]]
"""Invited committee POSITION turns inside the LangGraph primary.

The owner's explicit ``DIRECTOR debate`` command reaches the Director through
the existing Slack gateway, which signs the parsed invitation with the rest of
the intake (``SlackIntake.primary_committee_debate``). The invitation grants
no authority: the plan still needs owner approval and budget. The Director
(the openai member) is always seated for its own independent position and
single full-weight vote; each invited member gets one more POSITION seat. All
seats are ordinary assignments in the primary plan: the same packet router,
admission, budget, three-attempt roster, transports, host bridge and persisted
result artifacts serve them; nothing here dispatches.

After the terminal review the live evidence handler reads each seat's
persisted result, validates its contribution and tallies it with the
charter's configured weights over the fixed configured total. Members that
were not invited are recorded as not seated, never as unavailable. A seat
that failed, was refused or is ambiguous never reaches the tally, and a debate
without the Director's contribution never completes. A replay reads the same
immutable artifacts, so a tally never calls a model.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from pydantic import ValidationError

from .models import AssignmentKind, DebateContribution, DiscussionPhase, PrimaryCommitteeDebate, TaskRecord
from .team_charter import AdvisoryVote, AdvisoryVoteTally, tally_advisory_votes, team_operating_charter

# The primary runtime's executing Director (core_inventory_ready's default).
PRIMARY_DIRECTOR_MEMBER = "openai"
_POSITION_FIELDS = ("rationale", "assumptions", "material_risks", "unknowns", "conditions_to_change")


class CommitteeContributionInvalid(ValueError):
    """A seat's persisted result cannot be counted; the debate fails closed."""

    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


class CommitteeInvitationRejected(ValueError):
    """An owner debate command that does not parse into a valid invitation."""


@dataclass(frozen=True)
class CommitteeRecord:
    seated: tuple[str, ...]
    not_seated: tuple[str, ...]
    votes: tuple[AdvisoryVote, ...]
    tally: AdvisoryVoteTally


def committee_seat_members(debate: PrimaryCommitteeDebate) -> tuple[str, ...]:
    """The Director first, then the invited members in invitation order."""

    return (PRIMARY_DIRECTOR_MEMBER, *debate.invited_members)


def committee_seats(record: TaskRecord) -> tuple:
    """The plan's POSITION assignments, in sequence order."""

    if record.plan is None or record.intake.primary_committee_debate is None:
        return ()
    return tuple(sorted((item for item in record.plan.assignments
                         if item.discussion_phase is DiscussionPhase.POSITION),
                        key=lambda item: item.sequence))


def committee_position_objective(debate: PrimaryCommitteeDebate) -> str:
    return "State one bounded committee position on this owner question: " + debate.question


def committee_position_constraints(record: TaskRecord, assignment) -> list[str]:
    """What a seat, or the reviewer synthesizing the seats, is told in its packet."""

    debate = record.intake.primary_committee_debate
    if debate is None:
        return []
    if assignment.kind is AssignmentKind.INDEPENDENT_REVIEW:
        return [
            "Committee synthesis: treat the POSITION results among your dependencies as "
            "evidence; report agreement, dissent, contradictions and unresolved risks. "
            "This review casts no committee vote.",
        ]
    if assignment.discussion_phase is not DiscussionPhase.POSITION:
        return []
    role = (
        "This is the Director's own independent position and its single full-weight vote; "
        "moderating or reviewing other results is not this contribution, and it abstains "
        "only by choosing abstain."
        if assignment.primary_provider_lane == PRIMARY_DIRECTOR_MEMBER
        else "Committee POSITION turn invited by the Director: state one position; never open, "
        "widen or route a debate."
    )
    return [
        role + " Take no action beyond gathering evidence.",
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
                    producer_lane: str | None) -> CommitteeRecord:
    """Seats, votes and tally from each seat's authenticated, persisted result.

    Only seated members cast a vote or an explicit abstention. The others are
    listed as not seated; they cast no weight and the configured total (the
    denominator) is unchanged.
    """

    debate = record.intake.primary_committee_debate
    if debate is None:
        raise CommitteeContributionInvalid("committee_debate_absent")
    seated = committee_seat_members(debate)
    seats = committee_seats(record)
    if tuple(seat.primary_provider_lane for seat in seats) != seated:
        raise CommitteeContributionInvalid("committee_seats_changed")
    votes: list[AdvisoryVote] = []
    for seat in seats:
        member = seat.primary_provider_lane
        packet = results.get(seat.sequence)
        if packet is None or packet.get("status") != "SUCCEEDED" or packet.get("provider") != member:
            # Only a counted contribution completes a debate; the Director's
            # absence is never read as an abstention.
            raise CommitteeContributionInvalid(
                "committee_director_contribution_missing" if member == PRIMARY_DIRECTOR_MEMBER
                else "committee_position_unresolved")
        contribution = committee_contribution(packet.get("outcome"), debate)
        votes.append(AdvisoryVote(
            member=member,
            disposition="abstain" if contribution.abstain else "vote",
            option=contribution.vote_option,
            rationale=contribution.rationale[:2_000],
            evidence=contribution.evidence,
            confidence=contribution.confidence,
            producer=member == producer_lane,
        ))
    not_seated = tuple(sorted(set(team_operating_charter()["committee"]["members"]) - set(seated)))
    return CommitteeRecord(seated, not_seated, tuple(votes),
                           tally_advisory_votes(votes, material_risk=debate.material_risk))


_COMMAND = re.compile(r"(?is)^\s*(?:/director|director)\s+debate\b(?P<body>.*)$")
_MEMBER_NAMES = {"zai": "zai", "z.ai": "zai", "claude": "claude", "cursor": "cursor", "grok": "grok"}
DEBATE_COMMAND_USAGE = (
    "DIRECTOR debate, then one line each: Question: …, Options: A | B (2-6), "
    "Invite: zai, claude, cursor or grok (the Director is always seated), and "
    "optionally Material risk: yes|no."
)


def committee_invitation_from_command(text: str) -> PrimaryCommitteeDebate | None:
    """Parse the owner's explicit debate command, or None for any other text.

    Only the Slack gateway calls this, for an owner message in the owner
    channel, and signs the result with the rest of the intake. The command
    selects seats and states the question; it grants no approval, budget,
    tool or data access, and anything that does not parse is refused.
    """

    match = _COMMAND.match(text)
    if match is None:
        return None
    fields: dict[str, str] = {}
    for line in match.group("body").splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        key = " ".join(key.lower().split())
        if not separator or key not in {"question", "options", "invite", "material risk"} or key in fields:
            raise CommitteeInvitationRejected("each line must be one known field, given once")
        fields[key] = value.strip()
    if not fields.get("question") or not fields.get("options") or not fields.get("invite"):
        raise CommitteeInvitationRejected("question, options and invite are required")
    invited = []
    for name in fields["invite"].split(","):
        member = _MEMBER_NAMES.get(name.strip().lower())
        if member is None:
            raise CommitteeInvitationRejected("only zai, claude, cursor or grok can be invited")
        invited.append(member)
    risk = fields.get("material risk", "no").lower()
    if risk not in {"yes", "no"}:
        raise CommitteeInvitationRejected("material risk must be yes or no")
    try:
        return PrimaryCommitteeDebate(
            question=fields["question"],
            vote_options=tuple(option.strip() for option in fields["options"].split("|")),
            material_risk=risk == "yes",
            invited_members=tuple(invited),
        )
    except ValidationError as exc:
        raise CommitteeInvitationRejected("the invitation is outside its bounds") from exc
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 05:30 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 06:40 UTC | END]]
