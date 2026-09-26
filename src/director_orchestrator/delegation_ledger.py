# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 07:05 UTC | BEGIN]]
# [[Z.ai | glm-5.3-flash via Claude Code | 2026-09-13 | 05:44 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-12 | 02:16 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 22:01 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 20:40 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:07 UTC | BEGIN]]
"""Phase 2 of human-out-of-the-loop operation: the audit record format.

``docs/human-out-of-the-loop-operation.md`` sequences this capability across six
phases. Phase 1 is the grant schema; this is phase 2, the audit ledger and record
format, and nothing beyond it.

**Phase 2 executes no decision.** Nothing here resolves an
``OWNER_DECISION_REQUIRED``, merges anything, posts anything, marks a pull
request ready, or evaluates live gates. Structural validation refuses records
that claim delegation without a grant snapshot, at least one ACCEPT entry,
and nonempty passing gate evidence. These are necessary format invariants,
never a finding that the live evidence bar or grant scope is satisfied. Actual
authorization and gate evaluation belong to later phases.

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
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping

from .delegation import DelegationConfigurationError, Tier1Class, parse_grant
from .interactions import canonical_content_digest


AUDIT_SCHEMA_VERSION = "director-delegation-audit/v1"

# Bounded on every axis, like the grant schema, so a hostile or corrupted row
# cannot exhaust the process before validation rejects it.
_MAX_RECORD_BYTES = 65_536
_MAX_VERDICTS = 16
_MAX_GATE_RESULTS = 64
_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:@+-]{1,128}$")
_CANDIDATE_SHA = re.compile(r"^[0-9a-f]{40}$")

# Ledger identity, not routing or authorization. Case is insignificant. The
# owner's 2026-09-09 ruling makes OpenAI, ChatGPT and Codex ONE provider even
# when a connector or historical Director label supplied the verdict.
_PROVIDER_ALIASES = {
    "openai": "openai", "openai_director": "openai", "openai-director": "openai",
    "chatgpt": "openai", "codex": "openai", "openai-codex": "openai",
    "openai_codex": "openai", "chatgpt-codex": "openai", "chatgpt_codex": "openai",
    "chatgpt-codex-connector": "openai", "chatgpt_codex_connector": "openai",
    "anthropic": "claude", "claude-code": "claude", "claude_code": "claude",
    "cursor-agent": "cursor", "cursor_agent": "cursor",
    "google": "gemini", "google-gemini": "gemini", "antigravity": "gemini",
    "pplx": "perplexity", "xai": "grok", "x.ai": "grok",
}


class DelegationAuditError(ValueError):
    """A record is malformed and is refused rather than repaired.

    Every rejection is total. Malformed types and outcomes are never coerced
    into shape. Supported provider aliases and valid timezone offsets are
    canonicalized explicitly; an untrustworthy field invalidates the record.
    """


class RecordedOutcome(StrEnum):
    """What the record is a record *of*.

    ``DELEGATED`` and ``REFUSED`` are the two the "Audit record" section names
    directly: "every delegated decision, and every refusal to make one".
    ``WOULD_DELEGATE`` and ``WOULD_STOP`` are the phase-3 shadow vocabulary from
    the implementation-phase table. They are listed here because they are record
    format, not behaviour; no code in phase 2 produces either one.

    ``PRE_AUTHORIZED`` records that an owner's standing grant pre-authorized
    executing one pre-execution tier-1 class, ``execute_small_read_only_task``.
    No verdict exists yet when it is written, so it carries none; the task's own
    independent review stays mandatory downstream. It is never ``DELEGATED``,
    so the ACCEPT rule for delegated decisions is untouched.
    """

    DELEGATED = "DELEGATED"
    REFUSED = "REFUSED"
    WOULD_DELEGATE = "WOULD_DELEGATE"
    WOULD_STOP = "WOULD_STOP"
    PRE_AUTHORIZED = "PRE_AUTHORIZED"


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
    try:
        if value.utcoffset() is None:
            raise ValueError("timezone has no UTC offset")
        # Freeze the instant, including for custom tzinfo and DST folds. Future
        # comparisons must not rely on mutable or broken timezone behavior.
        return value.astimezone(timezone.utc)
    except Exception as exc:
        raise DelegationAuditError(f"{field} has an invalid UTC offset") from exc


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
        provider = self.provider.casefold()
        object.__setattr__(self, "provider", _PROVIDER_ALIASES.get(provider, provider))
        if not isinstance(self.outcome, VerdictOutcome):
            raise DelegationAuditError("verdict outcome is not a known outcome")
        if self.run_id is not None:
            _require_identifier(self.run_id, "verdict run_id")
        elif self.outcome.is_verdict:
            raise DelegationAuditError("a verdict must carry its run identifier")
        object.__setattr__(
            self, "observed_at", _require_aware(self.observed_at, "verdict observed_at")
        )

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
        object.__setattr__(
            self, "evaluated_at", _require_aware(self.evaluated_at, "gate evaluated_at")
        )

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

    ``candidate_sha`` is explicitly ``None`` (JSON ``null``) when a ``REFUSED``
    or ``WOULD_STOP`` outcome could not resolve a candidate. The field remains
    required; no commit is invented. ``DELEGATED`` and ``WOULD_DELEGATE`` always
    require a full exact commit SHA.

    ``grant_snapshot`` holds the canonical, validated grant document whenever
    a grant is named. Its contents are covered by the record digest and external
    ledger anchor; changing or removing configuration cannot change old evidence.
    An identifier alone is never accepted as a grant binding.

    ``verdicts`` and ``gate_results`` may both be empty in refusals. That is not an
    oversight: a refusal where no provider answered and no gate was reachable is
    the case the document warns hardest about, and the record has to be able to
    state it plainly rather than omit it.
    """

    schema_version: str
    outcome: RecordedOutcome
    grant_id: str | None
    decision_class: Tier1Class | str
    candidate_sha: str | None
    acting_agent: str
    verdicts: tuple[VerdictEntry, ...]
    gate_results: tuple[GateResult, ...]
    recorded_at: datetime
    grant_snapshot: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != AUDIT_SCHEMA_VERSION:
            raise DelegationAuditError("unsupported audit record schema")
        if not isinstance(self.outcome, RecordedOutcome):
            raise DelegationAuditError("outcome is not a known recorded outcome")
        if self.grant_id is not None:
            _require_identifier(self.grant_id, "grant_id")
        claims_delegation = self.outcome in (
            RecordedOutcome.DELEGATED, RecordedOutcome.WOULD_DELEGATE
        )
        if claims_delegation and self.grant_id is None:
            raise DelegationAuditError("delegation outcomes must name a grant")
        pre_authorized = self.outcome is RecordedOutcome.PRE_AUTHORIZED
        small_class = self.decision_class is Tier1Class.EXECUTE_SMALL_READ_ONLY_TASK
        # The pre-execution class never mints a merge-style record, and a
        # pre-authorization exists for that class alone.
        if claims_delegation and small_class:
            raise DelegationAuditError(
                "the small read-only class is pre-authorized, never delegated"
            )
        if pre_authorized and not small_class:
            raise DelegationAuditError("only the small read-only class is pre-authorized")
        if pre_authorized and self.grant_id is None:
            raise DelegationAuditError("a pre-authorization must name a grant")
        if not isinstance(self.decision_class, Tier1Class):
            if claims_delegation:
                raise DelegationAuditError("delegation decision_class is not a tier-1 class")
            # A bounded exact attempted class, including tier 0 or unknown, is
            # evidence of a refusal. It adds nothing to the delegable enum.
            _require_identifier(self.decision_class, "refused decision_class")
        if self.candidate_sha is None:
            if claims_delegation or pre_authorized:
                raise DelegationAuditError("delegation outcomes require a resolved candidate_sha")
        elif not isinstance(self.candidate_sha, str) or not _CANDIDATE_SHA.fullmatch(
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
        # the same provider" are not independent verdicts, so a record that
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
        # Preserve every evaluation, including a repeated name whose status
        # changed after a verdict. Collapsing this history conceals trip evidence.

        object.__setattr__(
            self, "recorded_at", _require_aware(self.recorded_at, "recorded_at")
        )
        if any(item.observed_at > self.recorded_at for item in self.verdicts):
            raise DelegationAuditError("verdict evidence is later than recorded_at")
        if any(item.evaluated_at > self.recorded_at for item in self.gate_results):
            raise DelegationAuditError("gate evidence is later than recorded_at")
        if claims_delegation:
            if len(self.verdicts) < 1 or any(
                not item.outcome.is_approval for item in self.verdicts
            ):
                raise DelegationAuditError(
                    "delegation requires at least one ACCEPT entry"
                )
            if not self.gate_results or any(
                not item.status.is_pass for item in self.gate_results
            ):
                raise DelegationAuditError("delegation requires nonempty passing gate evidence")
        if pre_authorized:
            # Written before execution: no verdict can exist yet, and every
            # term evaluated at that point must have passed.
            if self.verdicts:
                raise DelegationAuditError("a pre-authorization precedes every verdict")
            if not self.gate_results or any(
                not item.status.is_pass for item in self.gate_results
            ):
                raise DelegationAuditError(
                    "a pre-authorization requires nonempty passing gate evidence"
                )

        if self.grant_id is None:
            if self.grant_snapshot is not None:
                raise DelegationAuditError("a grant snapshot must name its grant")
        else:
            if not isinstance(self.grant_snapshot, str):
                raise DelegationAuditError("a named grant requires its snapshot")
            try:
                grant = parse_grant(self.grant_snapshot)
            except (DelegationConfigurationError, RecursionError, MemoryError, UnicodeError) as exc:
                raise DelegationAuditError("grant snapshot is malformed") from exc
            if grant is None or grant.grant_id != self.grant_id:
                raise DelegationAuditError("grant snapshot does not match grant_id")
            if pre_authorized and Tier1Class.EXECUTE_SMALL_READ_ONLY_TASK not in grant.classes:
                raise DelegationAuditError("grant snapshot does not name the pre-authorized class")
            # Validate and freeze contents, without deciding whether the grant
            # is live, in scope, unexhausted, or authorizes any actual action.
            object.__setattr__(self, "grant_snapshot", json.dumps(
                json.loads(self.grant_snapshot), ensure_ascii=True,
                sort_keys=True, separators=(",", ":"),
            ))

    def to_payload(self) -> dict[str, Any]:
        """The record as plain JSON-safe data, in the order the document names."""

        return {
            "schema_version": self.schema_version,
            "outcome": self.outcome.value,
            "grant_id": self.grant_id,
            "grant_snapshot": self.grant_snapshot,
            "decision_class": str(self.decision_class),
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

        This is a content identity and uniqueness key, not an integrity anchor
        by itself. The store verifies the full chain against separately protected
        external authority before returning evidence.
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
                "recorded_at", "grant_snapshot",
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
        except ValueError:
            # Construction permits exact unknown class names only in refusals.
            decision_class = raw_class

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
        if candidate_sha is not None and not isinstance(candidate_sha, str):
            raise DelegationAuditError("candidate_sha must be a string or null")

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
            grant_snapshot=fields["grant_snapshot"],
        )


def parse_record(document: str) -> DelegationAuditRecord:
    """Parse one stored record. Bounded, then validated, then rebuilt.

    The ledger read path goes through here rather than trusting its own rows: a
    row that has been altered outside the append path is exactly what an audit
    ledger has to be able to notice.
    """

    if not isinstance(document, str):
        raise DelegationAuditError("audit record must be a JSON string")
    try:
        if len(document.encode("utf-8")) > _MAX_RECORD_BYTES:
            raise DelegationAuditError("audit record exceeds its size bound")
        payload = json.loads(document)
    except (json.JSONDecodeError, RecursionError, MemoryError, UnicodeError) as exc:
        raise DelegationAuditError("audit record is not valid JSON") from exc
    return DelegationAuditRecord.from_payload(payload)
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:07 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 20:40 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 22:01 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-12 | 02:16 UTC | END]]
# [[Z.ai | glm-5.3-flash via Claude Code | 2026-09-13 | 05:44 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 07:05 UTC | END]]
