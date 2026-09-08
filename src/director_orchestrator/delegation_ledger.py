# [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:07 UTC | BEGIN]]
"""Phase 2 of human-out-of-the-loop operation: the audit record format.

``docs/human-out-of-the-loop-operation.md`` sequences this capability across six
phases. Phase 1 is the grant schema; this is phase 2, the audit ledger and record
format, and nothing beyond it.

**Phase 2 executes no decision.** Nothing here resolves an
``OWNER_DECISION_REQUIRED``, merges anything, posts anything, marks a pull
request ready, or consults the evidence bar. There is deliberately no function
that counts concurring verdicts or decides whether a gate set is sufficient:
that is the evidence bar, it belongs to phase 4, and writing it here would
defeat the sequencing the document relies on for safety. What exists is a
record format and a place to append it. Phases 3 to 5 write into it.

The document's "Audit record" section fixes the field set::

    Every delegated decision, and every refusal to make one, writes an
    immutable record containing the grant identifier, the decision class, the
    exact candidate commit, each verdict with its provider and run identifier,
    every gate result consulted, the acting agent identity, and timestamps.

Two properties follow from the rest of the document and shape everything below.

*Append only.* The record is "immutable" and is written "before the action, not
after it". There is therefore no update path and no delete path anywhere in this
module or in the ledger table it is stored in. A correction is a new record.
Mutability would make the ledger state rather than evidence, and the document
lists "the audit ledger cannot be written" as a trip condition precisely because
an unreviewable autonomous action is what the framework exists to prevent.

*Absence of evidence is never consent.* A provider that is out of quota,
unauthenticated, silent, or absent has not approved anything. ``VerdictOutcome``
therefore keeps those states distinct from every real verdict, and only
``ACCEPT`` answers ``is_approval``. The same holds for gates: ``GateStatus``
separates missing, stale, ambiguous, unavailable and unreadable from ``PASS``,
because the document sends every one of them to ``OWNER_DECISION_REQUIRED``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping

from .delegation import Tier1Class
from .interactions import canonical_content_digest


AUDIT_SCHEMA_VERSION = "director-delegation-audit/v1"

# Bounded on every axis, like the grant schema, so a hostile or corrupted row
# cannot exhaust the process before validation rejects it.
_MAX_RECORD_BYTES = 65_536
_MAX_VERDICTS = 16
_MAX_GATE_RESULTS = 64
_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:@+-]{1,128}$")
_CANDIDATE_SHA = re.compile(r"^[0-9a-f]{40}$")


class DelegationAuditError(ValueError):
    """A record is malformed and is refused rather than repaired.

    Every rejection is total. There is no partially valid record, and no input
    is coerced into shape: a record that cannot be trusted in one field cannot
    be trusted as evidence at all.
    """


class RecordedOutcome(StrEnum):
    """What the record is a record *of*.

    ``DELEGATED`` and ``REFUSED`` are the two the "Audit record" section names
    directly: "every delegated decision, and every refusal to make one".
    ``WOULD_DELEGATE`` and ``WOULD_STOP`` are the phase-3 shadow vocabulary from
    the implementation-phase table. They are listed here because they are record
    format, not behaviour; no code in phase 2 produces either one.
    """

    DELEGATED = "DELEGATED"
    REFUSED = "REFUSED"
    WOULD_DELEGATE = "WOULD_DELEGATE"
    WOULD_STOP = "WOULD_STOP"


class VerdictOutcome(StrEnum):
    """One provider's answer, or the reason it did not give one.

    The first three are the charter's review verdicts, spelled exactly as
    ``oracle_merge.OracleReviewVerdict`` spells them so a reader comparing a
    ledger row against a review artifact sees the same tokens.

    The rest are not verdicts. The document names each of them as a way a
    delegation attempt has actually failed in this project -- "providers have
    returned ambiguous results, failed authentication, hit usage limits, or been
    absent from the environment" -- and states that a provider which is "out of
    quota, unauthenticated, or silent has not approved anything, and its silence
    may not be counted, inferred, or waited out". Keeping them as distinct
    members is what stops a later reader from collapsing any of them into
    consent. ``is_approval`` is the single place that judgement is expressed.
    """

    ACCEPT = "ACCEPT"
    CHANGES_REQUIRED = "CHANGES_REQUIRED"
    SERIOUS_SAFETY_STOP = "SERIOUS_SAFETY_STOP"
    AMBIGUOUS = "AMBIGUOUS"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    SILENT = "SILENT"
    ABSENT = "ABSENT"

    @property
    def is_verdict(self) -> bool:
        """Whether a provider actually answered. Silence is not an answer."""

        return self in _REAL_VERDICTS

    @property
    def is_approval(self) -> bool:
        """Only ``ACCEPT`` approves. Everything else, including silence, does not."""

        return self is VerdictOutcome.ACCEPT


_REAL_VERDICTS = frozenset(
    {
        VerdictOutcome.ACCEPT,
        VerdictOutcome.CHANGES_REQUIRED,
        VerdictOutcome.SERIOUS_SAFETY_STOP,
    }
)


class GateStatus(StrEnum):
    """The result of one gate that was consulted.

    ``PASS`` and ``FAIL`` are the ordinary two. The remaining five are the exact
    conditions the evidence bar names -- "any element that is missing, stale,
    ambiguous, unavailable, or unreadable sends the decision to
    ``OWNER_DECISION_REQUIRED``" -- kept separate for the same reason the
    non-verdict outcomes are: a gate nobody could read is not a gate that
    passed, and the record has to be able to say which one it was.
    """

    PASS = "PASS"
    FAIL = "FAIL"
    MISSING = "MISSING"
    STALE = "STALE"
    AMBIGUOUS = "AMBIGUOUS"
    UNAVAILABLE = "UNAVAILABLE"
    UNREADABLE = "UNREADABLE"

    @property
    def is_pass(self) -> bool:
        """Only ``PASS`` passes. Unreadable, missing and stale do not."""

        return self is GateStatus.PASS


def _require_identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise DelegationAuditError(f"{field} is not a bounded identifier")
    return value


def _require_aware(value: object, field: str) -> datetime:
    """Require a timezone-aware datetime. A naive one is refused, not assumed.

    Assuming UTC for a naive timestamp would silently invent a fact. The
    document's trip conditions turn on ordering -- "a gate result changes after
    a verdict was recorded" -- so a timestamp whose offset was guessed is worse
    than no timestamp at all.
    """

    if not isinstance(value, datetime) or value.tzinfo is None:
        raise DelegationAuditError(f"{field} must be a timezone-aware datetime")
    return value


def _parse_aware(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise DelegationAuditError(f"{field} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise DelegationAuditError(f"{field} is not ISO-8601") from exc
    return _require_aware(parsed, field)


def _require_exact_fields(payload: object, expected: set[str], what: str) -> Mapping[str, Any]:
    """Require an exact field set, never a superset.

    An unrecognised member may be something the writer expected to take effect.
    Ignoring it would make the record claim less than whoever produced it
    believed it claimed, so the record is refused instead.
    """

    if not isinstance(payload, Mapping):
        raise DelegationAuditError(f"{what} must be one JSON object")
    if set(payload) != expected:
        raise DelegationAuditError(f"{what} fields are not exact")
    return payload


@dataclass(frozen=True, slots=True)
class VerdictEntry:
    """One provider's verdict, or its recorded failure to supply one.

    Covers "each verdict with its provider and run identifier". ``run_id`` is
    optional only because a provider that never ran has no run identifier; an
    ``ACCEPT`` without one is refused, because a verdict nobody can trace back
    to a run is not independently checkable and the ledger exists to be checked.
    """

    provider: str
    run_id: str | None
    outcome: VerdictOutcome
    observed_at: datetime

    def __post_init__(self) -> None:
        _require_identifier(self.provider, "verdict provider")
        if not isinstance(self.outcome, VerdictOutcome):
            raise DelegationAuditError("verdict outcome is not a known outcome")
        if self.run_id is not None:
            _require_identifier(self.run_id, "verdict run_id")
        elif self.outcome.is_verdict:
            raise DelegationAuditError("a verdict must carry its run identifier")
        _require_aware(self.observed_at, "verdict observed_at")

    def to_payload(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "run_id": self.run_id,
            "outcome": self.outcome.value,
            "observed_at": self.observed_at.isoformat(),
        }

    @classmethod
    def from_payload(cls, payload: object) -> "VerdictEntry":
        fields = _require_exact_fields(
            payload, {"provider", "run_id", "outcome", "observed_at"}, "verdict"
        )
        run_id = fields["run_id"]
        if run_id is not None and not isinstance(run_id, str):
            raise DelegationAuditError("verdict run_id must be a string or null")
        raw_outcome = fields["outcome"]
        if not isinstance(raw_outcome, str):
            raise DelegationAuditError("verdict outcome must be a string")
        try:
            outcome = VerdictOutcome(raw_outcome)
        except ValueError as exc:
            # Refused, never mapped to a neighbour.  An unknown outcome mapped
            # to ACCEPT invents consent; mapped to anything else it hides a
            # verdict that was actually given.
            raise DelegationAuditError("verdict outcome is not a known outcome") from exc
        return cls(
            provider=_require_identifier(fields["provider"], "verdict provider"),
            run_id=run_id,
            outcome=outcome,
            observed_at=_parse_aware(fields["observed_at"], "verdict observed_at"),
        )


@dataclass(frozen=True, slots=True)
class GateResult:
    """One gate that was consulted, and what it said when it was consulted.

    Covers "every gate result consulted". ``evaluated_at`` is what makes the
    "a gate result changes after a verdict was recorded" trip condition
    checkable by a reader who was not present when the record was written.
    """

    gate: str
    status: GateStatus
    evaluated_at: datetime

    def __post_init__(self) -> None:
        _require_identifier(self.gate, "gate name")
        if not isinstance(self.status, GateStatus):
            raise DelegationAuditError("gate status is not a known status")
        _require_aware(self.evaluated_at, "gate evaluated_at")

    def to_payload(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "status": self.status.value,
            "evaluated_at": self.evaluated_at.isoformat(),
        }

    @classmethod
    def from_payload(cls, payload: object) -> "GateResult":
        fields = _require_exact_fields(
            payload, {"gate", "status", "evaluated_at"}, "gate result"
        )
        raw_status = fields["status"]
        if not isinstance(raw_status, str):
            raise DelegationAuditError("gate status must be a string")
        try:
            status = GateStatus(raw_status)
        except ValueError as exc:
            raise DelegationAuditError("gate status is not a known status") from exc
        return cls(
            gate=_require_identifier(fields["gate"], "gate name"),
            status=status,
            evaluated_at=_parse_aware(fields["evaluated_at"], "gate evaluated_at"),
        )


@dataclass(frozen=True, slots=True)
class DelegationAuditRecord:
    """One immutable audit record. Construction validates; there is no setter.

    Every field maps to one clause of the document's "Audit record" sentence:

    ==========================  =================================================
    field                       clause
    ==========================  =================================================
    ``grant_id``                "the grant identifier"
    ``decision_class``          "the decision class"
    ``candidate_sha``           "the exact candidate commit"
    ``verdicts``                "each verdict with its provider and run identifier"
    ``gate_results``            "every gate result consulted"
    ``acting_agent``            "the acting agent identity"
    ``recorded_at``             "and timestamps"
    ==========================  =================================================

    ``outcome`` distinguishes the two things the same sentence says are
    recorded: a delegated decision, and a refusal to make one. ``schema_version``
    pins the format, matching the grant schema in phase 1.

    ``grant_id`` is nullable because a refusal frequently has no grant to name --
    the grant was absent, malformed, expired, or silent on the class. ``None``
    means exactly that and must never be read as a grant that applied.

    ``verdicts`` and ``gate_results`` may both be empty. That is not an
    oversight: a refusal where no provider answered and no gate was reachable is
    the case the document warns hardest about, and the record has to be able to
    state it plainly rather than omit it.
    """

    schema_version: str
    outcome: RecordedOutcome
    grant_id: str | None
    decision_class: Tier1Class
    candidate_sha: str
    acting_agent: str
    verdicts: tuple[VerdictEntry, ...]
    gate_results: tuple[GateResult, ...]
    recorded_at: datetime

    def __post_init__(self) -> None:
        if self.schema_version != AUDIT_SCHEMA_VERSION:
            raise DelegationAuditError("unsupported audit record schema")
        if not isinstance(self.outcome, RecordedOutcome):
            raise DelegationAuditError("outcome is not a known recorded outcome")
        if self.grant_id is not None:
            _require_identifier(self.grant_id, "grant_id")
        if not isinstance(self.decision_class, Tier1Class):
            raise DelegationAuditError("decision_class is not a tier-1 class")
        if not isinstance(self.candidate_sha, str) or not _CANDIDATE_SHA.fullmatch(
            self.candidate_sha
        ):
            raise DelegationAuditError("candidate_sha is not a full 40-hex commit")
        _require_identifier(self.acting_agent, "acting_agent")

        if not isinstance(self.verdicts, tuple) or any(
            not isinstance(item, VerdictEntry) for item in self.verdicts
        ):
            raise DelegationAuditError("verdicts must be a tuple of verdict entries")
        if len(self.verdicts) > _MAX_VERDICTS:
            raise DelegationAuditError("verdicts exceed their bound")
        # One entry per provider.  The document is explicit that "two runs of
        # the same provider" are not two independent verdicts, so a record that
        # could list a provider twice is a record a later reader could miscount
        # as two.  A second run against the same candidate is a new record.
        providers = [item.provider for item in self.verdicts]
        if len(set(providers)) != len(providers):
            raise DelegationAuditError("a provider may appear once per record")

        if not isinstance(self.gate_results, tuple) or any(
            not isinstance(item, GateResult) for item in self.gate_results
        ):
            raise DelegationAuditError("gate_results must be a tuple of gate results")
        if len(self.gate_results) > _MAX_GATE_RESULTS:
            raise DelegationAuditError("gate results exceed their bound")
        gates = [item.gate for item in self.gate_results]
        if len(set(gates)) != len(gates):
            raise DelegationAuditError("a gate may appear once per record")

        _require_aware(self.recorded_at, "recorded_at")

    def to_payload(self) -> dict[str, Any]:
        """The record as plain JSON-safe data, in the order the document names."""

        return {
            "schema_version": self.schema_version,
            "outcome": self.outcome.value,
            "grant_id": self.grant_id,
            "decision_class": self.decision_class.value,
            "candidate_sha": self.candidate_sha,
            "acting_agent": self.acting_agent,
            "verdicts": [item.to_payload() for item in self.verdicts],
            "gate_results": [item.to_payload() for item in self.gate_results],
            "recorded_at": self.recorded_at.isoformat(),
        }

    def canonical_json(self) -> str:
        """Byte-stable serialisation. Two equal records serialise identically."""

        return json.dumps(
            self.to_payload(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

    @property
    def digest(self) -> str:
        """The record's identity, derived from its content.

        Using the content digest rather than an assigned id is what makes a
        record independently checkable: a reader recomputes it from the stored
        row and learns whether the row still says what it said when it was
        written. It is also the ledger's uniqueness key, so an identical record
        cannot be appended twice.
        """

        return canonical_content_digest(self.canonical_json())

    @classmethod
    def from_payload(cls, payload: object) -> "DelegationAuditRecord":
        """Rebuild a record from data. Every malformed shape raises."""

        fields = _require_exact_fields(
            payload,
            {
                "schema_version", "outcome", "grant_id", "decision_class",
                "candidate_sha", "acting_agent", "verdicts", "gate_results",
                "recorded_at",
            },
            "audit record",
        )
        if fields["schema_version"] != AUDIT_SCHEMA_VERSION:
            raise DelegationAuditError("unsupported audit record schema")

        raw_outcome = fields["outcome"]
        if not isinstance(raw_outcome, str):
            raise DelegationAuditError("outcome must be a string")
        try:
            outcome = RecordedOutcome(raw_outcome)
        except ValueError as exc:
            raise DelegationAuditError("outcome is not a known recorded outcome") from exc

        raw_class = fields["decision_class"]
        if not isinstance(raw_class, str):
            raise DelegationAuditError("decision_class must be a string")
        try:
            decision_class = Tier1Class(raw_class)
        except ValueError as exc:
            # Tier 1 is exhaustive and fails closed.  A record naming a class
            # that is not on that list is a record about something tier 0, and
            # accepting it would let the ledger imply an authority that does
            # not exist.
            raise DelegationAuditError("decision_class is not a tier-1 class") from exc

        grant_id = fields["grant_id"]
        if grant_id is not None and not isinstance(grant_id, str):
            raise DelegationAuditError("grant_id must be a string or null")

        raw_verdicts = fields["verdicts"]
        if not isinstance(raw_verdicts, list):
            raise DelegationAuditError("verdicts must be a list")
        raw_gates = fields["gate_results"]
        if not isinstance(raw_gates, list):
            raise DelegationAuditError("gate_results must be a list")

        candidate_sha = fields["candidate_sha"]
        if not isinstance(candidate_sha, str):
            raise DelegationAuditError("candidate_sha must be a string")

        return cls(
            schema_version=AUDIT_SCHEMA_VERSION,
            outcome=outcome,
            grant_id=grant_id,
            decision_class=decision_class,
            candidate_sha=candidate_sha,
            acting_agent=_require_identifier(fields["acting_agent"], "acting_agent"),
            verdicts=tuple(VerdictEntry.from_payload(item) for item in raw_verdicts),
            gate_results=tuple(GateResult.from_payload(item) for item in raw_gates),
            recorded_at=_parse_aware(fields["recorded_at"], "recorded_at"),
        )


def parse_record(document: str) -> DelegationAuditRecord:
    """Parse one stored record. Bounded, then validated, then rebuilt.

    The ledger read path goes through here rather than trusting its own rows: a
    row that has been altered outside the append path is exactly what an audit
    ledger has to be able to notice.
    """

    if not isinstance(document, str):
        raise DelegationAuditError("audit record must be a JSON string")
    if len(document.encode("utf-8")) > _MAX_RECORD_BYTES:
        raise DelegationAuditError("audit record exceeds its size bound")
    try:
        payload = json.loads(document)
    except json.JSONDecodeError as exc:
        raise DelegationAuditError("audit record is not valid JSON") from exc
    return DelegationAuditRecord.from_payload(payload)
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:07 UTC | END]]
