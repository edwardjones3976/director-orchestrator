# [[Claude Code | Claude Opus 5 | 2026-09-08 | 16:10 UTC | BEGIN]]
"""Phase 1 of human-out-of-the-loop operation: grant schema and readiness only.

``docs/human-out-of-the-loop-operation.md`` sequences this capability across six
phases and states that each is separately built, reviewed and accepted, that no
phase begins before its predecessor is accepted, and that acceptance of one
grants nothing to the next. This module is phase 1 and nothing beyond it.

**It executes no decision.** There is no code here that resolves an
``OWNER_DECISION_REQUIRED``, merges anything, posts anything, or consults the
evidence bar. Phase 1 is the grant schema, its validation, and the readiness
booleans that let an operator see the configured state. The audit ledger is
phase 2, shadow recording is phase 3, and the first phase that resolves a real
decision is phase 4. Building any of those here would defeat the sequencing the
document relies on for safety.

Everything defaults off. A deployment is ``ATTENDED`` unless a grant is present
and valid, and it returns to ``ATTENDED`` whenever configuration is absent,
invalid, expired, or unreadable.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping


GRANT_SCHEMA_VERSION = "director-delegation-grant/v1"

# Bounded so a malformed or hostile configuration cannot exhaust the process
# before validation rejects it.
_MAX_GRANT_BYTES = 16_384
_MAX_PROVIDERS = 16
_MAX_EXERCISES = 1_000
_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:@+-]{1,128}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9._-]{1,64}/[A-Za-z0-9._-]{1,64}$")
# One trailing "*" only.  A general glob would let a branch pattern widen by
# accident, and the document requires a grant to be bounded on every axis.
_BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9._/-]{1,128}\*?$")


class DelegationState(StrEnum):
    """Exactly one state is in force per deployment profile at any time."""

    ATTENDED = "ATTENDED"
    DELEGATED = "DELEGATED"
    SUSPENDED = "SUSPENDED"


class Tier1Class(StrEnum):
    """The exhaustive tier-1 enumeration, one member per named act.

    The document is explicit that tier 1 is closed: there is no residual or
    catch-all class, and none may be added by describing a decision as routine.
    A category whose membership is a judgement call is not a closed enumeration
    and would silently reopen the fail-closed rule, so this enum is the whole
    list and an unknown value is rejected rather than mapped to a neighbour.
    """

    MERGE_UNPROTECTED_BRANCH = "merge_pull_request_into_non_protected_branch"
    MARK_PULL_REQUEST_READY = "mark_pull_request_ready_for_review"
    POST_TO_DESIGNATED_CHANNEL = "post_to_project_designated_slack_channel"


class DelegationConfigurationError(ValueError):
    """A grant is absent, malformed, or otherwise not usable.

    Callers treat this as ``ATTENDED``. It is deliberately not distinguished
    from "no grant configured": a deployment that cannot read its grant and a
    deployment that has none must behave identically.
    """


@dataclass(frozen=True, slots=True)
class DelegationGrant:
    """One owner-written grant, bounded on every axis the document names.

    Construction validates. There is no partially valid grant: either every
    bound is present and well formed, or ``DelegationConfigurationError`` is
    raised and the deployment stays ``ATTENDED``.
    """

    schema_version: str
    grant_id: str
    repository: str
    branch_pattern: str
    classes: frozenset[Tier1Class]
    providers: frozenset[str]
    expires_at: datetime
    max_exercises: int
    revoked: bool

    def __post_init__(self) -> None:
        if self.schema_version != GRANT_SCHEMA_VERSION:
            raise DelegationConfigurationError("unsupported delegation grant schema")
        if not _IDENTIFIER.fullmatch(self.grant_id):
            raise DelegationConfigurationError("grant_id is not a bounded identifier")
        if not _REPOSITORY.fullmatch(self.repository):
            raise DelegationConfigurationError("repository must be owner/name")
        if not _BRANCH_PATTERN.fullmatch(self.branch_pattern):
            raise DelegationConfigurationError("branch_pattern is not bounded")
        if not self.classes:
            raise DelegationConfigurationError("a grant must name at least one class")
        if not self.providers or len(self.providers) > _MAX_PROVIDERS:
            raise DelegationConfigurationError("eligible providers are unbounded")
        if any(not _IDENTIFIER.fullmatch(item) for item in self.providers):
            raise DelegationConfigurationError("provider identity is not bounded")
        # The evidence bar needs two concurring ACCEPTs from different
        # providers, neither a material author.  A grant naming fewer than two
        # eligible providers can never satisfy it, so it is refused at
        # configuration time rather than failing later per decision.
        if len(self.providers) < 2:
            raise DelegationConfigurationError(
                "a grant must name at least two eligible providers"
            )
        if self.expires_at.tzinfo is None:
            raise DelegationConfigurationError("expires_at must be timezone aware")
        if isinstance(self.max_exercises, bool) or not isinstance(
            self.max_exercises, int
        ):
            raise DelegationConfigurationError("max_exercises must be an integer")
        if not 1 <= self.max_exercises <= _MAX_EXERCISES:
            raise DelegationConfigurationError("max_exercises is out of bounds")

    def covers(self, decision: Tier1Class, *, repository: str, branch: str) -> bool:
        """Whether this grant names the exact class, repository and branch.

        Grants do not compose, inherit, or extend by analogy: authority for one
        repository, class, or assignment is never authority for another. A grant
        silent on the class being decided does not apply.
        """

        if decision not in self.classes or repository != self.repository:
            return False
        if self.branch_pattern.endswith("*"):
            return branch.startswith(self.branch_pattern[:-1])
        return branch == self.branch_pattern

    def live_at(self, moment: datetime) -> bool:
        """Whether the grant is unexpired and unrevoked at ``moment``.

        Exercise count is deliberately not consulted here. It is ledger state,
        and the ledger is phase 2; a phase-1 reader must not imply it has one.
        """

        if moment.tzinfo is None:
            raise DelegationConfigurationError("moment must be timezone aware")
        return not self.revoked and moment < self.expires_at


def parse_grant(document: str | None) -> DelegationGrant | None:
    """Parse one owner-written grant, or return ``None`` for "no delegation".

    ``None`` means the deployment is ``ATTENDED``. Every malformed input raises
    ``DelegationConfigurationError``, which callers also treat as ``ATTENDED``,
    so a broken grant can never read as a working one.
    """

    if document is None or not document.strip():
        return None
    if len(document.encode("utf-8")) > _MAX_GRANT_BYTES:
        raise DelegationConfigurationError("delegation grant exceeds its size bound")
    try:
        payload = json.loads(document)
    except json.JSONDecodeError as exc:
        raise DelegationConfigurationError("delegation grant is not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise DelegationConfigurationError("delegation grant must be one JSON object")

    expected = {
        "schema_version", "grant_id", "repository", "branch_pattern",
        "classes", "providers", "expires_at", "max_exercises", "revoked",
    }
    if set(payload) != expected:
        # Exact field set, not a superset: an unrecognised member may be a
        # widening someone expected to take effect, and silently ignoring it
        # would be worse than refusing the grant.
        raise DelegationConfigurationError("delegation grant fields are not exact")

    raw_classes = payload["classes"]
    if not isinstance(raw_classes, list) or not raw_classes:
        raise DelegationConfigurationError("classes must be a non-empty list")
    try:
        classes = frozenset(Tier1Class(item) for item in raw_classes)
    except ValueError as exc:
        # An unknown class is refused rather than dropped.  Dropping it would
        # quietly narrow a grant the owner believed was wider, and mapping it to
        # a neighbour would widen one they believed was narrower.
        raise DelegationConfigurationError("classes name a non tier-1 decision") from exc

    raw_providers = payload["providers"]
    if not isinstance(raw_providers, list):
        raise DelegationConfigurationError("providers must be a list")

    expires_raw = payload["expires_at"]
    if not isinstance(expires_raw, str):
        raise DelegationConfigurationError("expires_at must be an ISO-8601 string")
    try:
        expires_at = datetime.fromisoformat(expires_raw)
    except ValueError as exc:
        raise DelegationConfigurationError("expires_at is not ISO-8601") from exc
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    revoked = payload["revoked"]
    if not isinstance(revoked, bool):
        raise DelegationConfigurationError("revoked must be a boolean")

    return DelegationGrant(
        schema_version=str(payload["schema_version"]),
        grant_id=str(payload["grant_id"]),
        repository=str(payload["repository"]),
        branch_pattern=str(payload["branch_pattern"]),
        classes=classes,
        providers=frozenset(str(item) for item in raw_providers),
        expires_at=expires_at,
        max_exercises=payload["max_exercises"],
        revoked=revoked,
    )


def resolve_state(
    grant: DelegationGrant | None,
    *,
    enabled: bool,
    suspended: bool,
    moment: datetime,
) -> DelegationState:
    """Resolve the operating state. ``ATTENDED`` unless everything holds.

    ``SUSPENDED`` wins over everything: the document states there is no path
    from ``SUSPENDED`` to ``DELEGATED`` that does not pass through an explicit
    owner action, so it is checked first and never inferred away.
    """

    if suspended:
        return DelegationState.SUSPENDED
    if not enabled or grant is None or not grant.live_at(moment):
        return DelegationState.ATTENDED
    return DelegationState.DELEGATED


def delegation_readiness(
    *,
    enabled: bool,
    suspended: bool,
    grant_document: str | None,
    moment: datetime,
) -> dict[str, Any]:
    """Readiness booleans for ``/health``. Names and booleans only.

    No grant identifier, repository, branch, provider, or expiry is reported.
    ``/health`` returns readiness booleans only and never secret or allowlist
    values, and a grant names real repositories and providers.

    ``executes_decisions`` is reported explicitly and is always false. An
    operator reading this endpoint should be able to see that phase 1 resolves
    nothing, rather than inferring it from the absence of a field.
    """

    try:
        grant = parse_grant(grant_document)
        malformed = False
    except DelegationConfigurationError:
        grant, malformed = None, True

    state = resolve_state(
        grant, enabled=enabled, suspended=suspended, moment=moment
    )
    return {
        "phase": 1,
        "executes_decisions": False,
        "state": state.value,
        "enabled_requested": bool(enabled),
        "suspended": bool(suspended),
        "grant_configured": grant is not None,
        "grant_malformed": malformed,
        "grant_live": bool(grant is not None and grant.live_at(moment)),
        "classes_configured": sorted(item.value for item in grant.classes)
        if grant is not None
        else [],
        "eligible_provider_count": len(grant.providers) if grant is not None else 0,
    }
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 16:10 UTC | END]]
