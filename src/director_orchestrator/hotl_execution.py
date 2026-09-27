# [[OpenAI Codex | GPT-6 | 2026-09-26 | 23:28 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 21:18 UTC | BEGIN]]
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
closed five-class list in docs/human-out-of-the-loop-operation.md; anything not
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

from dataclasses import asdict, dataclass, field, replace
from contextlib import closing
from datetime import datetime, timezone
from enum import StrEnum
import fnmatch
import copy
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from typing import Callable

from .merge_eligibility import MergeEvidenceBundle, MergeEligibilityPolicy, evaluate_merge_eligibility


STANDING_MERGE_AUTHORITY = "owner-merge-law-2026-09-26"
MERGE_CLASSES = frozenset({
    "eligible_merge_non_protected_branch", "eligible_merge_protected_branch",
})


# Closed, and ordered as the document orders it. Membership is literal.
TIER_ONE_CLASSES: frozenset[str] = frozenset(
    {
        "eligible_merge_non_protected_branch",
        "eligible_merge_protected_branch",
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
#   authorization  item 9   standing merge authority active, or live scoped grant
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
#   eligibility            --       policy.small_read_only_eligible() admits it: no
#                                   deterministic approval reason (a keyword scan)
#                                   and a read-shaped request; this is not item 7,
#                                   which nothing evaluates before execution
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
    merged_sha: str | None = None

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
    operating_state: str = "ATTENDED",
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
    if action.action_class in MERGE_CLASSES:
        return Decision(Outcome.HOLD, action, 1,
                        ("merge execution requires authenticated boundary evidence",),
                        evaluated_at=now, terms=states)
    if operating_state != "DELEGATED":
        return Decision(Outcome.HOLD, action, 1,
                        ("non-merge class requires a live grant in DELEGATED state",),
                        evaluated_at=now, terms=states)
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


@dataclass(frozen=True, kw_only=True)
class MergeObservation:
    """Trusted observer's signed readback, never a provider/model payload.

    Reuses the existing review/CI/security evidence bundle. The observer must
    authenticate its source records, enumerate all contributors and both rename
    paths, resolve identity aliases, and read the live owner authority, ledgers,
    forge protections/rulesets and authenticated merge credential. Its signing
    key belongs to that trusted observer, never the author or dispatch caller.
    The accepting review must carry literal ACCEPT. Only after checking it does
    the boundary normalize to APPROVED for the existing eligibility evaluator.
    """

    bundle: MergeEvidenceBundle
    pull_number: int
    base_sha: str
    environment: str
    observed_at: datetime
    challenge: str
    authority_id: str
    authority_status: str
    operating_state: str
    producer_principal: str
    merging_principal: str
    provenance_status: str
    protection_status: str
    ruleset_status: str
    bypass_status: str
    cancellation_status: str
    tier_zero_status: str
    budget_used: int
    budget_limit: int
    attempts_used: int
    attempt_limit: int
    hops_used: int
    hop_limit: int
    base_protected: bool

    def payload(self) -> bytes:
        def encode(value: object) -> object:
            if isinstance(value, (set, frozenset)):
                return sorted(value)
            if isinstance(value, datetime):
                return value.isoformat()
            raise TypeError("unsupported merge evidence value")

        return json.dumps(asdict(self), default=encode, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")


class MergeExecutionBoundary:
    """One authenticated observation route, one guarded mutation, no retries.

    Dependencies are trusted application wiring, not request parameters. The
    observer returns (MergeObservation, HMAC-SHA256 hex signature); a digest set
    or a TermEvidence(True) supplied by a caller cannot authorize execution.
    The transport must bind expected head/base and acting principal and refuse
    all protection/ruleset bypass, including implicit administrator bypass.
    No transport is selected or activated by standing owner authority alone.
    """

    def __init__(
        self, *, repository: str, environment: str, assignment_id: str,
        policy: MergeEligibilityPolicy, prohibited_paths: tuple[str, ...],
        observer_key: bytes,
        observe: Callable[[ProposedAction, int, str], tuple[MergeObservation, str]],
        merge: Callable[..., str], audit_path: str,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        if (len(observer_key) < 32 or not repository or not environment or not assignment_id
                or not audit_path or audit_path == ":memory:"):
            raise ValueError("merge boundary requires trusted identity, key and durable audit")
        self.repository, self.environment = repository, environment
        self.assignment_id = assignment_id
        self.policy, self.prohibited_paths = policy, prohibited_paths
        self._key, self._observe, self._merge = observer_key, observe, merge
        self._audit_path, self._clock = audit_path, clock

    def _read(self, action: ProposedAction, pull_number: int) -> MergeObservation:
        challenge = secrets.token_hex(32)
        observation, signature = self._observe(action, pull_number, challenge)
        observation = copy.deepcopy(observation)
        payload = observation.payload()
        if len(payload) > 1_048_576:
            raise ValueError("merge evidence exceeds its bound")
        expected = hmac.new(self._key, payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError("merge observation is not authenticated")
        if observation.challenge != challenge:
            raise ValueError("merge observation was replayed instead of revalidated")
        return observation

    def _reasons(self, action: ProposedAction, pull: int, obs: MergeObservation) -> list[str]:
        bundle = obs.bundle
        reasons: list[str] = []
        if (action.action_class not in MERGE_CLASSES
                or action.repository != self.repository or bundle.repository != self.repository
                or action.environment != self.environment or obs.environment != self.environment
                or bundle.assignment_status.get("assignment_id") != self.assignment_id
                or type(pull) is not int or pull < 1
                or type(obs.pull_number) is not int or obs.pull_number != pull
                or bundle.candidate_head_sha != action.candidate_sha):
            reasons.append("candidate or repository scope mismatch")
        if (obs.observed_at.tzinfo is None
                or not 0 <= (self._clock() - obs.observed_at).total_seconds() <= 30):
            reasons.append("merge observation is stale or future dated")
        if (obs.authority_id != STANDING_MERGE_AUTHORITY or obs.authority_status != "ACTIVE"
                or obs.operating_state not in {"ATTENDED", "DELEGATED"}):
            reasons.append("standing owner merge authority unavailable, revoked or suspended")
        if (not obs.producer_principal.strip() or not obs.merging_principal.strip()
                or obs.provenance_status != "COMPLETE"
                or obs.merging_principal != action.acting_identity
                or obs.merging_principal == obs.producer_principal
                or obs.merging_principal in bundle.material_author_principals):
            reasons.append("merging identity is unbound, producer or material contributor")
        if (type(obs.base_protected) is not bool
                or obs.base_protected != (action.action_class == "eligible_merge_protected_branch")
                or not re.fullmatch(r"[0-9a-f]{40}", obs.base_sha)
                or obs.protection_status != "SATISFIED" or obs.ruleset_status != "SATISFIED"
                or obs.bypass_status != "DISABLED"):
            reasons.append("branch protections or rulesets unverified, unsatisfied or bypassed")
        if obs.cancellation_status != "RUNNING" or obs.tier_zero_status != "CLEAR":
            reasons.append("cancelled, ambiguous or unrelated tier-0 action implicated")
        for used, limit in ((obs.budget_used, obs.budget_limit),
                            (obs.attempts_used, obs.attempt_limit), (obs.hops_used, obs.hop_limit)):
            if type(used) is not int or type(limit) is not int or not 0 <= used < limit:
                reasons.append("budget, attempt or hop bound exhausted or unknown")
        for path in bundle.changed_paths:
            if (not path or path.startswith("/") or "\\" in path
                    or any(part in {"", ".", ".."} for part in path.split("/"))
                    or ":" in path
                    or any(fnmatch.fnmatchcase(path, pattern) for pattern in self.prohibited_paths)):
                reasons.append("changed path is unsafe or prohibited")
        # Include the producer even when it has no counted lines. The signed
        # complete provenance inventory, not line counts, determines authorship.
        reviews = bundle.independent_reviews
        if (len(reviews) != 1 or reviews[0].verdict != "ACCEPT"
                or reviews[0].authenticated_principal != action.acting_identity):
            reasons.append("merger lacks its own unambiguous exact-head ACCEPT")
        bundle = replace(
            bundle, material_author_principals=(
                bundle.material_author_principals | {obs.producer_principal}),
            independent_reviews=tuple(replace(review, verdict="APPROVED")
                                      if review.verdict == "ACCEPT" else review for review in reviews),
        )
        # The standing law supplies merge authorization only. All other policy
        # switches, paths, required checks and assignment gates remain in force.
        verdict = evaluate_merge_eligibility(
            bundle, policy=replace(self.policy, require_owner_authorization=False))
        reasons.extend(verdict["blocking_reasons"])
        if any(check.trusted_app_verified is not True for check in bundle.checks):
            reasons.append("CI producer identity is unverified or malformed")
        if action.acting_identity not in verdict["independent_review_principals"]:
            reasons.append("merger lacks its own unambiguous exact-head ACCEPT")
        # Invalid or contradictory records cannot hide beside a valid security record.
        if (not bundle.security_reviews
                or len({review.record_digest for review in bundle.security_reviews}) != len(bundle.security_reviews)
                or any(
                review.verdict != "APPROVED"
                or not review.authenticated_principal.strip()
                or review.reviewed_head_sha != action.candidate_sha
                or review.authenticated_principal in bundle.material_author_principals
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", review.authentication_record_digest)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", review.record_digest)
                or review.authentication_record_digest not in bundle.authenticated_record_digests
                or review.record_digest not in bundle.authenticated_record_digests
                for review in bundle.security_reviews)):
            reasons.append("security evidence is ambiguous or unauthenticated")
        return reasons

    def _audit(self, action: ProposedAction, pull: int, payload: bytes) -> None:
        # A unique reservation survives crashes and ambiguous network outcomes.
        # No automatic release: reconcile the existing attempt before any retry.
        with closing(sqlite3.connect(self._audit_path, timeout=5)) as connection, connection:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("""CREATE TABLE IF NOT EXISTS standing_merge_attempt (
                repository TEXT NOT NULL, pull INTEGER NOT NULL, head TEXT NOT NULL,
                evidence BLOB NOT NULL, PRIMARY KEY (repository, pull, head))""")
            for verb in ("UPDATE", "DELETE"):
                connection.execute(
                    f"CREATE TRIGGER IF NOT EXISTS standing_merge_no_{verb.lower()} "
                    f"BEFORE {verb} ON standing_merge_attempt BEGIN "
                    "SELECT RAISE(ABORT, 'merge audit is append-only'); END")
            connection.execute("INSERT INTO standing_merge_attempt VALUES (?, ?, ?, ?)",
                               (action.repository, pull, action.candidate_sha, payload))

    def execute(self, action: ProposedAction, pull_number: int) -> Decision:
        """Revalidate and merge exactly once; a HOLD never authorizes a retry."""
        def hold(*reasons: str) -> Decision:
            return Decision(Outcome.HOLD, action, classify(action), tuple(reasons),
                            evaluated_at=self._clock())

        if action.action_class not in MERGE_CLASSES:
            return hold("standing authority covers pull-request merge execution only")
        try:
            observed = self._read(action, pull_number)
            reasons = self._reasons(action, pull_number, observed)
            if reasons:
                return hold(*reasons)
            audit_payload = json.dumps({
                "action": asdict(action), "observation": json.loads(observed.payload()),
                "signature": hmac.new(self._key, observed.payload(), hashlib.sha256).hexdigest(),
            }, sort_keys=True, separators=(",", ":")).encode("utf-8")
            self._audit(action, pull_number, audit_payload)
            final = self._read(action, pull_number)
            reasons = self._reasons(action, pull_number, final)
            # Only the fresh challenge and timestamp may change after reservation.
            if replace(final, observed_at=observed.observed_at,
                       challenge=observed.challenge).payload() != observed.payload():
                reasons.append("candidate or evidence changed during final revalidation")
            if reasons:
                return hold(*reasons)
            merged_sha = self._merge(
                repository=self.repository, pull_number=pull_number,
                expected_head_sha=action.candidate_sha, expected_base_sha=final.base_sha,
                acting_identity=action.acting_identity, allow_bypass=False)
            if not isinstance(merged_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", merged_sha):
                return hold("merge result ambiguous; reconcile reserved attempt, never replay")
        except Exception as exc:
            # Do not expose transport errors that may contain credentials.
            return hold(f"merge boundary refused or result ambiguous: {type(exc).__name__}; reconcile before retry")
        return Decision(Outcome.EXECUTE, action, 1, evaluated_at=self._clock(),
                        terms={term: True for term in TERMS}, merged_sha=merged_sha)


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
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 21:18 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 23:28 UTC | END]]
