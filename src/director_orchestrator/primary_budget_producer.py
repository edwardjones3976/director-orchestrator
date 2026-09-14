# [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 08:50 UTC | BEGIN]]
"""Unsigned primary-provider-budget/v1 preparation and already-signed import.

Two narrowly scoped operations live here, matching the existing contracts in
``antigravity_team.py`` exactly rather than inventing a new authority scheme:

* ``prepare_unsigned_budget_intent`` builds a canonical, unsigned document.
  An owner-controlled signer can later turn it into a real ``ProviderBudget``
  by computing an HMAC over exactly this document's ``canonical_document()``
  and attaching the result as ``owner_signature``. Preparation reads only its
  explicit arguments: no secret, no database connection, no provider request,
  and no inference of any limit, identity, grant, approval, or account
  entitlement.
* ``verify_signed_budget`` and ``import_signed_budget`` accept an externally
  supplied, already-signed ``ProviderBudget`` and either check its signature
  or register it through the existing ``PrimaryProviderBudgetAuthority`` on a
  connection and transaction the caller owns. Neither function signs
  anything, loads a secret, opens a connection, or constructs authority: the
  caller supplies an already-initialized ``PrimaryProviderBudgetAuthority``
  (itself built from an already-initialized trusted
  ``EntitlementControlPlaneStore`` and a ``primary_schema``) and, for import,
  a transaction it owns.

``verify_signed_budget`` is signature-and-structure only: a structurally
valid but expired (not-today) document can still verify. Import eligibility
-- the current UTC day and the current active project registration/ceiling
-- is a separate, later check inside ``import_signed_budget``, taken after
the per-project advisory lock and re-run on every successful import,
including an exact-repeat idempotent one. A type annotation is never trusted
as a runtime guarantee: every signed object is revalidated from its raw
field values (never from a possibly unvalidated ``model_construct``ed or
``model_copy``ed instance, and never through a lossy dump/reparse round
trip) before it is verified, imported, or reported idempotent.

This module grants no signing, live bootstrap, or production secret-loading
authority. It never reserves an attempt, marks a budget ACTIVE, dispatches,
creates usage/spend, grants approval, or releases a reservation; those stay
the exclusive, unchanged responsibility of ``PrimaryProviderBudgetAuthority``
and the primary admission path. "Prepared", "verified", and "registered" are
kept as distinct statuses below and are never conflated with each other or
with the primary attempt lifecycle. Nothing here ever prints or re-raises an
exception's raw input value; see ``scripts/prepare_primary_provider_budget.py``
for the same discipline at the CLI boundary.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, ValidationError, field_validator

from .antigravity_team import (
    ClosedModel,
    Digest,
    Identifier,
    PrimaryProviderBudgetAuthority,
    ProviderBudget,
    TeamAuthorizationError,
)
from .entitlement_contracts import document_digest


class BudgetProducerStatus(StrEnum):
    PREPARED = "PREPARED"
    VERIFIED = "VERIFIED"
    REGISTERED = "REGISTERED"
    ALREADY_REGISTERED = "ALREADY_REGISTERED"


def _require_actual_calendar_day(day: str) -> None:
    """A real UTC calendar date, not merely four-two-two digit-shaped text."""
    try:
        date.fromisoformat(day)
    except ValueError as exc:
        raise TeamAuthorizationError(
            "day must be an actual UTC calendar date, not merely digit-shaped"
        ) from exc


class UnsignedProviderBudgetIntent(ClosedModel):
    """A pure, unsigned candidate for a future ``ProviderBudget``.

    Deliberately carries no ``owner_signature`` field at all -- not a
    zero-filled or placeholder one -- so it can never itself validate as a
    ``ProviderBudget`` and can never be mistaken for signed authority.
    """

    schema_version: Literal["primary-provider-budget/v1"]
    grant_digest: Digest
    provider_id: Identifier
    project_id: Identifier
    task_id: Identifier
    day: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    task_limit_microusd: Annotated[int, Field(gt=0)]
    daily_limit_microusd: Annotated[int, Field(gt=0)]
    attempt_reservation_microusd: Annotated[int, Field(gt=0)]
    token_limit: Annotated[int, Field(gt=0)]

    @field_validator("day")
    @classmethod
    def _validate_calendar_day(cls, value: str) -> str:
        _require_actual_calendar_day(value)
        return value

    def canonical_document(self) -> dict[str, Any]:
        """Byte-for-byte what an owner signer would compute its HMAC over."""
        return self.model_dump(mode="json")

    @property
    def digest(self) -> str:
        return document_digest(self.canonical_document())


def prepare_unsigned_budget_intent(
    *,
    grant_digest: str,
    provider_id: str,
    project_id: str,
    task_id: str,
    day: str,
    task_limit_microusd: int,
    daily_limit_microusd: int,
    attempt_reservation_microusd: int,
    token_limit: int,
    schema_version: str = "primary-provider-budget/v1",
) -> UnsignedProviderBudgetIntent:
    """Validate explicit nonsecret fields and return the canonical unsigned intent.

    Deterministic and side-effect free: the same arguments always produce the
    same document and the same digest, and nothing here reads a secret, opens
    a database connection, or calls a provider.
    """
    return UnsignedProviderBudgetIntent(
        schema_version=schema_version,
        grant_digest=grant_digest,
        provider_id=provider_id,
        project_id=project_id,
        task_id=task_id,
        day=day,
        task_limit_microusd=task_limit_microusd,
        daily_limit_microusd=daily_limit_microusd,
        attempt_reservation_microusd=attempt_reservation_microusd,
        token_limit=token_limit,
    )


@dataclass(frozen=True, slots=True)
class ProviderBudgetVerification:
    """Evidence that a signed document verified. Not a registration receipt.

    Signature-and-structure only: a structurally valid but expired (not the
    current UTC day) document can still be ``VERIFIED``. Import eligibility
    is checked separately, only inside ``import_signed_budget``.
    """

    status: BudgetProducerStatus
    authority_digest: str
    provider_id: str
    project_id: str
    task_id: str
    day: str


@dataclass(frozen=True, slots=True)
class ProviderBudgetImportReceipt:
    """Evidence that a signed document is now the one authoritative row for
    its ``grant_digest``.

    Never implies an attempt reservation, ACTIVE status, dispatch, usage,
    spend, or approval: those stay in the existing admission path, untouched
    by this producer.
    """

    status: BudgetProducerStatus
    authority_digest: str
    provider_id: str
    project_id: str
    task_id: str
    day: str


def _require_typed_authority(authority: Any) -> PrimaryProviderBudgetAuthority:
    if not isinstance(authority, PrimaryProviderBudgetAuthority):
        raise TeamAuthorizationError(
            "typed enrolled provider budget authority required"
        )
    return authority


def _require_structurally_valid_budget(budget: ProviderBudget) -> ProviderBudget:
    """Revalidate every field from scratch. A type annotation is not a runtime
    guarantee, and ``ProviderBudget.model_validate`` short-circuits and hands
    back the same object, unrevalidated, whenever it already is a
    ``ProviderBudget`` (including one built by ``model_construct`` or a
    ``model_copy`` of one) -- so validation is forced here on a plain
    ``dict`` of the object's raw field values instead, never on the instance
    itself and never through a dump/reparse round trip (serializing an
    out-of-range value such as a ``bool`` into its declared ``int`` field can
    coerce it into something that re-parses as valid, which would silently
    launder exactly the object this function exists to reject).

    Also enforces the same real-calendar-day rule ``UnsignedProviderBudgetIntent``
    enforces, since ``ProviderBudget`` itself only pattern-matches the shape of
    ``day``, not its validity as an actual date.

    Raises before any signature check, lock, query, or import decision. Never
    accepts, normalizes, or signs a changed document.
    """
    if not isinstance(budget, ProviderBudget):
        raise TeamAuthorizationError("a typed ProviderBudget is required")
    try:
        raw_fields = dict(budget)
    except (AttributeError, TypeError) as exc:
        raise TeamAuthorizationError(
            "signed budget failed strict structural validation"
        ) from exc
    try:
        revalidated = ProviderBudget.model_validate(raw_fields, strict=True)
    except ValidationError as exc:
        raise TeamAuthorizationError(
            "signed budget failed strict structural validation"
        ) from exc
    if revalidated != budget:
        raise TeamAuthorizationError(
            "signed budget failed strict structural validation"
        )
    _require_actual_calendar_day(revalidated.day)
    return revalidated


def verify_signed_budget(
    budget: ProviderBudget, *, authority: PrimaryProviderBudgetAuthority
) -> ProviderBudgetVerification:
    """Verify an externally supplied signed budget and report nothing else.

    Reads no secret of its own: it uses the secret already held by the
    caller-injected authority's entitlement store, and it writes nothing.
    Signature-and-structure only -- see ``ProviderBudgetVerification``.
    """
    authority = _require_typed_authority(authority)
    budget = _require_structurally_valid_budget(budget)
    budget.verify(authority.entitlements._owner_authority_secret)
    return ProviderBudgetVerification(
        status=BudgetProducerStatus.VERIFIED,
        authority_digest=budget.grant_digest,
        provider_id=budget.provider_id,
        project_id=budget.project_id,
        task_id=budget.task_id,
        day=budget.day,
    )


def _require_current_utc_day(
    authority: PrimaryProviderBudgetAuthority, budget: ProviderBudget
) -> None:
    """Mirror ``PrimaryProviderBudgetAuthority.reserve``'s own UTC-rollover check.

    A signed budget can verify -- ``VERIFIED`` is signature-and-structure
    only -- while no longer naming today's UTC day. That document is not
    eligible to be imported or reported idempotent right now, even though
    its signature and shape are still genuinely valid.
    """
    current_day = authority.entitlements._clock().astimezone(timezone.utc).date().isoformat()
    if budget.day != current_day:
        raise TeamAuthorizationError("provider budget day is not the current UTC day")


def _require_current_project_ceiling(
    connection: Any, authority: PrimaryProviderBudgetAuthority, budget: ProviderBudget
) -> None:
    """Mirror ``PrimaryProviderBudgetAuthority.register``'s own ceiling check.

    Reads the same latest non-superseded project registration row
    ``register`` itself reads and never pins to a specific
    ``registration_generation``, so a compatible project update -- one that
    does not lower the ceiling below this budget -- is not rejected. Applies
    on a repeat/idempotent import exactly as on a fresh one, so a
    registration that went missing or a ceiling that tightened after the
    original import is caught before an idempotent receipt is ever reported,
    not only before a fresh insert.
    """
    registration = connection.execute(
        f"SELECT document FROM {authority.entitlements.schema}.entitlement_project_registration "
        "WHERE project_profile_id=%s AND superseded_at IS NULL "
        "ORDER BY registration_generation DESC LIMIT 1",
        (budget.project_id,),
    ).fetchone()
    if registration is None:
        raise TeamAuthorizationError("registered project budget required")
    limits = registration["document"]
    task_ceiling = int(Decimal(str(limits["budget_task_usd"])) * 1_000_000)
    daily_ceiling = int(Decimal(str(limits["budget_daily_usd"])) * 1_000_000)
    if budget.task_limit_microusd > task_ceiling or budget.daily_limit_microusd > daily_ceiling:
        raise TeamAuthorizationError("budget exceeds signed project registration")


def import_signed_budget(
    *,
    authority: PrimaryProviderBudgetAuthority,
    connection: Any,
    budget: ProviderBudget,
) -> ProviderBudgetImportReceipt:
    """Register an already-signed ``ProviderBudget``, or report an exact repeat.

    The caller owns ``connection`` and its transaction boundary; this
    function opens no connection of its own, loads no secret, and never
    reserves an attempt, marks ACTIVE, dispatches, records usage/spend,
    grants approval, or releases a reservation.

    Order, all under the one per-project advisory lock
    ``PrimaryProviderBudgetAuthority.register`` itself also takes (reentrant
    within one transaction, so taking it twice here does not self-block):

    1. Verify signature and structure (``verify_signed_budget``).
    2. Take the per-project lock.
    3. Recheck current eligibility: today's UTC day and the current active
       project registration/ceiling. Signature verification is never
       equated with current eligibility.
    4. Look up every existing row for this exact consumer key
       (task/provider/project/day) and, separately, any existing row for
       this exact ``grant_digest``.
    5. If a row for this ``grant_digest`` exists: its content must match
       exactly, or the import is rejected as a conflict. If it matches,
       ambiguity is rejected -- any *other* digest also matching this
       consumer key -- before an ``ALREADY_REGISTERED`` receipt is
       returned. Ambiguity is checked before idempotency is reported, never
       after.
    6. Otherwise (a fresh ``grant_digest``): any existing row at all under
       this consumer key is a conflict, checked before insertion. Only then
       does the existing ``PrimaryProviderBudgetAuthority.register`` perform
       the actual insert, together with its own signature, registered-project
       ceiling, and per-task/day ceiling-drift checks.

    No row is ever updated or deleted here; only ``SELECT`` and the existing
    ``register``'s own ``INSERT`` run.
    """
    authority = _require_typed_authority(authority)
    verification = verify_signed_budget(budget, authority=authority)
    budget = _require_structurally_valid_budget(budget)
    connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ("primary-provider-budget:" + budget.project_id,),
    )
    _require_current_utc_day(authority, budget)
    _require_current_project_ceiling(connection, authority, budget)

    consumer_matches = connection.execute(
        f"SELECT authority_digest FROM {authority.schema}.director_primary_provider_budget "
        "WHERE document->>'task_id'=%s AND document->>'provider_id'=%s "
        "AND document->>'project_id'=%s AND document->>'day'=%s",
        (budget.task_id, budget.provider_id, budget.project_id, budget.day),
    ).fetchall()
    own_row = connection.execute(
        f"SELECT document FROM {authority.schema}.director_primary_provider_budget "
        "WHERE authority_digest=%s",
        (budget.grant_digest,),
    ).fetchone()

    if own_row is not None:
        prior = ProviderBudget.model_validate_json(json.dumps(own_row["document"]))
        if prior != budget:
            raise TeamAuthorizationError(
                "a different signed budget document is already registered "
                "for this grant digest"
            )
        ambiguous = [
            row for row in consumer_matches
            if row["authority_digest"] != budget.grant_digest
        ]
        if ambiguous:
            raise TeamAuthorizationError(
                "a different signed budget is already the authoritative "
                "consumer match for this task/provider/project/day"
            )
        return ProviderBudgetImportReceipt(
            status=BudgetProducerStatus.ALREADY_REGISTERED,
            authority_digest=verification.authority_digest,
            provider_id=verification.provider_id,
            project_id=verification.project_id,
            task_id=verification.task_id,
            day=verification.day,
        )

    if consumer_matches:
        raise TeamAuthorizationError(
            "a different signed budget is already the authoritative consumer "
            "match for this task/provider/project/day"
        )
    authority.register(connection, budget)
    return ProviderBudgetImportReceipt(
        status=BudgetProducerStatus.REGISTERED,
        authority_digest=verification.authority_digest,
        provider_id=verification.provider_id,
        project_id=verification.project_id,
        task_id=verification.task_id,
        day=verification.day,
    )
# [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 08:50 UTC | END]]
