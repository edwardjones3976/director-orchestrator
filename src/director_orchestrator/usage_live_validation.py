"""Fail-closed evidence rules for isolated live-validation usage checks.

The Windows validation script mirrors these rules. Keep both paths aligned: a
cooling provider may only count as ready when its latest record is inside the
15-minute window and status is exactly ok or prompted.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence


PRIMARY_USAGE_PROVIDERS: tuple[str, ...] = ("claude", "cursor", "openai")
SUCCESS_STATUSES = frozenset({"ok", "prompted"})
COOLDOWN_WINDOW = timedelta(minutes=15)


class UsageLiveValidationError(ValueError):
    """Raised when live-validation evidence is missing, stale, or unsuccessful."""


def _provider_names(values: Iterable[Any] | None) -> list[str]:
    if values is None:
        return []
    return [str(item) for item in values]


def validate_provider_partition(
    *,
    scheduled_providers: Sequence[Any] | None,
    cooling_down_providers: Sequence[Any] | None,
    expected_primary: Sequence[str] = PRIMARY_USAGE_PROVIDERS,
) -> tuple[list[str], list[str]]:
    """Require disjoint scheduled/cooling sets that exactly cover expected primaries."""
    scheduled = _provider_names(scheduled_providers)
    cooling = _provider_names(cooling_down_providers)
    expected = [str(item) for item in expected_primary]

    if len(scheduled) != len(set(scheduled)):
        raise UsageLiveValidationError("scheduled_providers contains duplicates")
    if len(cooling) != len(set(cooling)):
        raise UsageLiveValidationError("cooling_down_providers contains duplicates")

    scheduled_sorted = sorted(scheduled)
    cooling_sorted = sorted(cooling)
    unknown = sorted((set(scheduled_sorted) | set(cooling_sorted)) - set(expected))
    if unknown:
        raise UsageLiveValidationError(
            "Unknown providers in schedule response: " + ", ".join(unknown)
        )

    overlap = sorted(set(scheduled_sorted) & set(cooling_sorted))
    if overlap:
        raise UsageLiveValidationError(
            "scheduled_providers and cooling_down_providers overlap: "
            + ", ".join(overlap)
        )

    covered = sorted(set(scheduled_sorted) | set(cooling_sorted))
    if covered != sorted(expected):
        raise UsageLiveValidationError(
            "scheduled_providers and cooling_down_providers must partition "
            f"expected primary providers {sorted(expected)}; got {covered}"
        )
    return scheduled_sorted, cooling_sorted


def _parse_checked_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        checked = value
    elif isinstance(value, str) and value.strip():
        checked = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise UsageLiveValidationError("checked_at is missing or not a timestamp")
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    return checked.astimezone(timezone.utc)


def index_unique_records_by_provider(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        provider = str(record.get("provider") or "")
        if not provider:
            raise UsageLiveValidationError("usage record missing provider")
        grouped.setdefault(provider, []).append(record)
    unique: dict[str, Mapping[str, Any]] = {}
    for provider, items in grouped.items():
        if len(items) != 1:
            raise UsageLiveValidationError(
                f"Ambiguous usage evidence for provider {provider}: "
                f"{len(items)} records"
            )
        unique[provider] = items[0]
    return unique


def assert_cooling_success_evidence(
    *,
    providers: Sequence[str],
    latest_records: Sequence[Mapping[str, Any]],
    now: datetime | None = None,
    cooldown_window: timedelta = COOLDOWN_WINDOW,
) -> list[Mapping[str, Any]]:
    """Fail closed unless each cooling provider has one recent ok/prompted record."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)

    wanted = [str(provider) for provider in providers]
    relevant = [
        record
        for record in latest_records
        if str(record.get("provider") or "") in set(wanted)
    ]
    by_provider = index_unique_records_by_provider(relevant)
    accepted: list[Mapping[str, Any]] = []
    for provider in wanted:
        record = by_provider.get(provider)
        if record is None:
            raise UsageLiveValidationError(
                f"Missing latest usage record for cooling provider {provider}"
            )
        status = str(record.get("status") or "")
        if status not in SUCCESS_STATUSES:
            raise UsageLiveValidationError(
                f"Cooling provider {provider} latest status is {status!r}; "
                "only ok/prompted evidence is acceptable"
            )
        checked_at = _parse_checked_at(record.get("checked_at"))
        age = current - checked_at
        # Match monitor due semantics: age >= window means due again, not cooling.
        if age < timedelta(0) or age >= cooldown_window:
            minutes = int(cooldown_window.total_seconds() // 60)
            raise UsageLiveValidationError(
                f"Cooling provider {provider} latest check is outside the "
                f"{minutes}-minute cooldown window"
            )
        accepted.append(record)
    return accepted


def assert_scheduled_cycle_evidence(
    *,
    providers: Sequence[str],
    cycle_id: str,
    records: Sequence[Mapping[str, Any]],
    started_at: datetime,
) -> list[Mapping[str, Any]]:
    """Fail closed unless each scheduled provider has one successful exact-cycle record."""
    if not cycle_id:
        raise UsageLiveValidationError("cycle_id is required for scheduled evidence")
    started = started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    else:
        started = started.astimezone(timezone.utc)

    wanted = [str(provider) for provider in providers]
    matched = []
    for record in records:
        if str(record.get("cycle_id") or "") != cycle_id:
            continue
        provider = str(record.get("provider") or "")
        if provider not in set(wanted):
            continue
        checked_at = _parse_checked_at(record.get("checked_at"))
        if checked_at < started:
            continue
        matched.append(record)

    by_provider = index_unique_records_by_provider(matched)
    accepted: list[Mapping[str, Any]] = []
    for provider in wanted:
        record = by_provider.get(provider)
        if record is None:
            raise UsageLiveValidationError(
                f"Missing exact-cycle usage record for scheduled provider {provider}"
            )
        status = str(record.get("status") or "")
        if status not in SUCCESS_STATUSES:
            raise UsageLiveValidationError(
                f"Scheduled provider {provider} status is {status!r}; "
                "only ok/prompted evidence is acceptable"
            )
        accepted.append(record)
    return accepted


def evaluate_usage_live_validation(
    *,
    run_status: str,
    scheduled_providers: Sequence[Any] | None,
    cooling_down_providers: Sequence[Any] | None,
    latest_records: Sequence[Mapping[str, Any]],
    cycle_id: str | None = None,
    started_at: datetime | None = None,
    now: datetime | None = None,
    expected_primary: Sequence[str] = PRIMARY_USAGE_PROVIDERS,
) -> list[Mapping[str, Any]]:
    """Evaluate not_due or accepted evidence and return the accepted record set."""
    status = str(run_status or "")
    scheduled, cooling = validate_provider_partition(
        scheduled_providers=scheduled_providers,
        cooling_down_providers=cooling_down_providers,
        expected_primary=expected_primary,
    )
    if status == "not_due":
        if scheduled:
            raise UsageLiveValidationError(
                "not_due responses must not list scheduled_providers"
            )
        return assert_cooling_success_evidence(
            providers=cooling,
            latest_records=latest_records,
            now=now,
        )
    if status == "accepted":
        if not scheduled:
            raise UsageLiveValidationError(
                "accepted responses must schedule at least one primary provider"
            )
        if started_at is None:
            raise UsageLiveValidationError("started_at is required for accepted cycles")
        if not cycle_id:
            raise UsageLiveValidationError("cycle_id is required for accepted cycles")
        scheduled_records = assert_scheduled_cycle_evidence(
            providers=scheduled,
            cycle_id=cycle_id,
            records=latest_records,
            started_at=started_at,
        )
        cooling_records = assert_cooling_success_evidence(
            providers=cooling,
            latest_records=latest_records,
            now=now,
        )
        return [*scheduled_records, *cooling_records]
    raise UsageLiveValidationError(f"Unsupported usage-check run status {status!r}")
