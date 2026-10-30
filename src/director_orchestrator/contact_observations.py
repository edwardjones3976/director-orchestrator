"""Passive, evidence-based observations for contacts the usage monitor does not probe.

WO-20261029-005. The monitor's SUPPORTED_PROVIDERS set covers the six
API/routine names it may probe; the six contacts here (Z.ai, GitHub
Copilot, Firecrawl, the Antigravity subscription CLI, Grok Bot and ZCode)
have no contact-specific coverage. This module adds it as a purely passive
reader: it derives one record per contact from existing provider-dispatch
receipt files on local disk. It never calls a provider, never opens a
socket, never runs a subprocess, and never reads environment values,
``.env`` files, tokens or raw prompt text; receipt content beyond the
machine-written status fields below is never copied into a record.

Honesty rules (owner direction 2026-10-28, request-provider-coverage):
- a contact with only an executable, route, catalog entry, configuration
  entry or key name present is NEVER ``ready``; ``ready`` requires a dated
  receipt showing a completed task result inside the freshness window;
- missing evidence yields ``not-observed`` in every state field;
- evidence older than ``CONTACT_OBSERVATION_FRESHNESS`` yields ``stale``;
- quota is reported only when a receipt itself states a limit (an observed
  limit, or an observed remaining amount). A successful task or a plan's
  existence never implies quota: the 2026-10-29 research receipt
  (firecrawl 20261029T164559Z) records Z.ai coding-plan balance errors on
  valid plans, and GitHub bills Copilot CLI prompts as premium requests,
  so quota must be observed per contact, never inferred.

Contact isolation: every contact reads only its own receipt directories,
and a JSON receipt is used only when its own ``provider`` value matches the
contact. Grok Bot (xAI family, Slack bot contact) never reads direct-Grok
``grok``/``xai`` receipts; ZCode never reads Z.ai API ``zai`` receipts; the
Antigravity subscription CLI never reads Gemini API ``gemini`` receipts.
No record is ever labeled as another provider's lane.

Host-provider bridge: the bridge (host_provider_bridge.py) exposes no
local status record this reader may consult without network I/O, so
bridge-sourced aspects stay ``not-observed``; adding them needs a passive,
persisted bridge status file, which does not exist today.

ZCode family label: Foreman ruling 2026-10-29 10:25 PDT records
provider_family = zai (Z.ai's own product). The contact_families table is
intentionally not extended here: tests/test_contact_families.py pins the
exact lane set and lies outside this order's permitted paths, so the ruled
label is recorded below (equal to ``provider_family_for("zai")``) and the
zcode lane keeps no family-independence credit of its own.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .contact_families import UnknownContactError, provider_family_for
from .models import (
    ContactAuthState,
    ContactObservation,
    ContactObservationStaleness,
    ContactObservationsReport,
    ContactPermissionState,
    ContactQuotaState,
    ContactTaskResultState,
)

#: A receipt older than this window no longer certifies freshness. Documented
#: freshness window: 7 days, chosen because these contacts are dispatched
#: intermittently (days apart) while a week still bounds actionable staleness.
CONTACT_OBSERVATION_FRESHNESS = timedelta(days=7)

#: Where provider-dispatch receipt files live on this host. Overridable per
#: call (and in tests); computed once at import, never from a request.
DEFAULT_RECEIPTS_ROOT = Path.home() / ".claude" / "skills" / "provider-dispatch" / "receipts"

#: Foreman ruling 2026-10-29 10:25 PDT: ZCode's provider family is Z.ai
#: (Z.ai's own product). Used only until the zcode lane joins the
#: contact_families table together with its exact-set consistency test.
ZCODE_PROVIDER_FAMILY = "Z.ai"

_ERROR_STATUSES = {"error", "failed", "failure", "timeout"}
_SUCCESS_STATUSES = {"completed", "success", "finished"}
_AUTH_FAILURE_MARKERS = (
    "401",
    "403",
    "unauthorized",
    "unauthenticated",
    "authentication",
    "invalid_api_key",
    "invalid api key",
    "invalid token",
)
_QUOTA_LIMIT_MARKERS = (
    "429",
    "rate_limit",
    "rate limit",
    "quota",
    "resource_exhausted",
    "insufficient",
    "limit_exceeded",
    "credits",
)
_STAMP_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$")


@dataclass(frozen=True)
class ContactSpec:
    """The static description of one observed contact.

    ``receipt_directories`` are exclusive to this contact (isolation), and a
    JSON receipt is accepted only when its ``provider`` value is one of
    ``receipt_providers``. ``family_lane`` resolves the provider-family label
    through contact_families; ``family_fallback`` is a Foreman-recorded label
    used while the lane is absent from that table (zcode only).
    """

    contact_id: str
    family_lane: str
    receipt_directories: tuple[str, ...]
    receipt_providers: frozenset[str]
    receipt_suffixes: tuple[str, ...]
    note: str
    family_fallback: str | None = None


_CONTACT_SPECS: tuple[ContactSpec, ...] = (
    ContactSpec(
        contact_id="zai",
        family_lane="zai",
        receipt_directories=("zai",),
        receipt_providers=frozenset({"zai"}),
        receipt_suffixes=(".json",),
        note="Z.ai (GLM) coding-plan API lane via provider-dispatch receipts. Quota is never inferred from plan presence.",
    ),
    ContactSpec(
        contact_id="github-copilot",
        family_lane="github-copilot",
        receipt_directories=("github-copilot",),
        receipt_providers=frozenset({"github-copilot"}),
        receipt_suffixes=(".json",),
        note=(
            "Observation only: issues-only specialist (AGENTS.md, owner direction "
            "2026-10-28); no execution, routing or producer capability. Premium-request "
            "quota stays not-observed unless a receipt states it."
        ),
    ),
    ContactSpec(
        contact_id="firecrawl",
        family_lane="firecrawl",
        receipt_directories=("firecrawl",),
        receipt_providers=frozenset({"firecrawl"}),
        receipt_suffixes=(".search.txt", ".scrape.txt"),
        note="Firecrawl research receipts; content is never copied into the record.",
    ),
    ContactSpec(
        contact_id="antigravity-subscription-cli",
        family_lane="antigravity",
        receipt_directories=("antigravity", "antigravity-subscription-cli", "agy"),
        receipt_providers=frozenset({"antigravity", "agy", "antigravity-subscription-cli"}),
        receipt_suffixes=(".json",),
        note=(
            "Antigravity subscription CLI contact (Google family). It is not the "
            "Gemini API lane and never reads gemini receipts."
        ),
    ),
    ContactSpec(
        contact_id="grok-bot",
        family_lane="grok-bot",
        receipt_directories=("grok-bot",),
        receipt_providers=frozenset({"grok-bot"}),
        receipt_suffixes=(".json",),
        note=(
            "Grok Bot (xAI family) is a separate contact from direct Grok; it never "
            "reads direct-Grok grok or xai receipts."
        ),
    ),
    ContactSpec(
        contact_id="zcode",
        family_lane="zcode",
        receipt_directories=("zcode",),
        receipt_providers=frozenset({"zcode"}),
        receipt_suffixes=(".json",),
        note=(
            "ZCode (Z.ai family per Foreman ruling 2026-10-29) is not the Z.ai API "
            "lane and never reads zai receipts."
        ),
        family_fallback=ZCODE_PROVIDER_FAMILY,
    ),
)

CONTACT_SPECS_BY_ID: dict[str, ContactSpec] = {spec.contact_id: spec for spec in _CONTACT_SPECS}

OBSERVED_CONTACT_IDS: tuple[str, ...] = tuple(CONTACT_SPECS_BY_ID)


@dataclass(frozen=True)
class _ReceiptEvidence:
    """The newest usable dated receipt found for one contact."""

    path: Path
    observed_at: datetime
    transport: str
    task_success: bool | None
    auth_failed: bool
    quota_limited: bool


def _parse_stamp(value: Any) -> datetime | None:
    """Parse a ``YYYYMMDDTHHMMSSZ`` receipt stamp into an aware UTC datetime."""

    if not isinstance(value, str):
        return None
    match = _STAMP_RE.match(value.strip())
    if match is None:
        return None
    year, month, day, hour, minute, second = (int(part) for part in match.groups())
    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    except ValueError:
        return None


def _filename_stamp(name: str) -> datetime | None:
    """Extract a leading ``YYYYMMDDTHHMMSSZ`` stamp from a receipt filename."""

    match = re.search(r"(\d{8}T\d{6}Z)", name)
    return _parse_stamp(match.group(1)) if match else None


def _stringify(payload: dict[str, Any], *names: str) -> str:
    parts = [
        str(payload.get(name))
        for name in names
        if isinstance(payload.get(name), (str, int, float, bool))
    ]
    return " ".join(parts).lower()


def _json_evidence(path: Path, spec: ContactSpec) -> _ReceiptEvidence | None:
    """Read one JSON receipt passively; None when it is unusable or another contact's."""

    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    provider = payload.get("provider")
    if provider not in spec.receipt_providers:
        # Another contact's receipt inside this directory is never evidence here.
        return None
    observed_at = (
        _parse_stamp(payload.get("finished_at"))
        or _parse_stamp(payload.get("started_at"))
        or _filename_stamp(path.name)
    )
    if observed_at is None:
        # Undated evidence can certify neither freshness nor a dated task result.
        return None
    exit_code = payload.get("exit_code")
    is_error = payload.get("is_error") is True
    status = payload.get("status")
    status_text = status.lower() if isinstance(status, str) else ""
    if is_error or (isinstance(exit_code, int) and exit_code != 0) or status_text in _ERROR_STATUSES:
        task_success: bool | None = False
    elif (exit_code == 0 and not is_error) or status_text in _SUCCESS_STATUSES:
        task_success = True
    else:
        # A dated receipt with no task-outcome fields certifies no task result.
        task_success = None
    failure_text = _stringify(payload, "error", "provider_error_code", "native_subtype", "status")
    return _ReceiptEvidence(
        path=path,
        observed_at=observed_at,
        transport=f"provider-dispatch receipt ({provider})",
        task_success=task_success,
        auth_failed=task_success is False and any(marker in failure_text for marker in _AUTH_FAILURE_MARKERS),
        quota_limited=task_success is False and any(marker in failure_text for marker in _QUOTA_LIMIT_MARKERS),
    )


def _text_evidence(path: Path, spec: ContactSpec) -> _ReceiptEvidence | None:
    """Read one dated text receipt (firecrawl search/scrape) passively.

    The file's own content is raw provider output and is never opened or
    copied; only its dated name and whether it recorded a result matter.
    """

    observed_at = _filename_stamp(path.name)
    if observed_at is None:
        return None
    try:
        recorded_result = path.stat().st_size > 0
    except OSError:
        return None
    kind = "search" if path.name.lower().endswith(".search.txt") else "scrape"
    return _ReceiptEvidence(
        path=path,
        observed_at=observed_at,
        transport=f"provider-dispatch receipt ({spec.contact_id} {kind})",
        task_success=recorded_result or None,
        auth_failed=False,
        quota_limited=False,
    )


def _latest_evidence(spec: ContactSpec, receipts_root: Path) -> _ReceiptEvidence | None:
    """The newest usable dated receipt for this contact, from its own directories only."""

    latest: _ReceiptEvidence | None = None
    for directory in spec.receipt_directories:
        lane = receipts_root / directory
        try:
            entries = list(lane.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.is_file():
                    continue
            except OSError:
                continue
            name = entry.name.lower()
            if name.endswith(".json"):
                evidence = _json_evidence(entry, spec)
            elif any(name.endswith(suffix) for suffix in spec.receipt_suffixes):
                evidence = _text_evidence(entry, spec)
            else:
                evidence = None
            if evidence is not None and (latest is None or evidence.observed_at > latest.observed_at):
                latest = evidence
    return latest


def _resolve_family(spec: ContactSpec) -> str:
    try:
        return provider_family_for(spec.family_lane)
    except UnknownContactError:
        if spec.family_fallback is not None:
            return spec.family_fallback
        raise


def _build_record(spec: ContactSpec, evidence: _ReceiptEvidence | None, *, now: datetime) -> ContactObservation:
    family = _resolve_family(spec)
    if evidence is None:
        staleness = ContactObservationStaleness.NOT_OBSERVED
        auth_state = ContactAuthState.NOT_OBSERVED
        quota_state = ContactQuotaState.NOT_OBSERVED
        task_state = ContactTaskResultState.NOT_OBSERVED
        source: str | None = None
        observed_at: datetime | None = None
        transport: str | None = None
    else:
        stale = (now - evidence.observed_at) > CONTACT_OBSERVATION_FRESHNESS
        staleness = ContactObservationStaleness.STALE if stale else ContactObservationStaleness.FRESH
        auth_state = (
            ContactAuthState.STALE
            if stale
            else ContactAuthState.OBSERVED_AUTHENTICATION_FAILED
            if evidence.auth_failed
            else ContactAuthState.OBSERVED_AUTHENTICATED
            if evidence.task_success is True
            else ContactAuthState.NOT_OBSERVED
        )
        quota_state = (
            ContactQuotaState.STALE
            if stale and evidence.quota_limited
            else ContactQuotaState.OBSERVED_LIMITED
            if evidence.quota_limited
            else ContactQuotaState.NOT_OBSERVED
        )
        task_state = (
            ContactTaskResultState.STALE
            if stale and evidence.task_success is not None
            else ContactTaskResultState.OBSERVED_FAILURE
            if evidence.task_success is False
            else ContactTaskResultState.OBSERVED_SUCCESS
            if evidence.task_success is True
            else ContactTaskResultState.NOT_OBSERVED
        )
        source = str(evidence.path)
        observed_at = evidence.observed_at
        transport = evidence.transport
    permissions_state = (
        # Standing policy record (AGENTS.md 2026-10-28), not dated receipt
        # evidence: the issues-only scope outlives any single receipt.
        ContactPermissionState.RECORDED_ISSUES_ONLY
        if spec.contact_id == "github-copilot"
        else ContactPermissionState.NOT_OBSERVED
    )
    return ContactObservation(
        contact_id=spec.contact_id,
        provider_family=family,
        source=source,
        observed_at_utc=observed_at,
        transport=transport,
        account_scope=None,
        auth_state=auth_state,
        quota_state=quota_state,
        permissions_state=permissions_state,
        task_result_state=task_state,
        staleness=staleness,
        # The only readiness input: a dated, fresh, successful task result.
        ready=(
            staleness is ContactObservationStaleness.FRESH
            and task_state is ContactTaskResultState.OBSERVED_SUCCESS
        ),
        note=spec.note,
    )


def contact_observation(
    contact_id: str,
    *,
    receipts_root: Path | str | None = None,
    now: datetime | None = None,
) -> ContactObservation:
    """Build one contact's observation record from existing receipts only."""

    try:
        spec = CONTACT_SPECS_BY_ID[contact_id]
    except KeyError as exc:
        raise KeyError(f"unknown observed contact: {contact_id!r}") from exc
    root = Path(receipts_root) if receipts_root is not None else DEFAULT_RECEIPTS_ROOT
    current = now or datetime.now(timezone.utc)
    return _build_record(spec, _latest_evidence(spec, root), now=current)


def contact_observations(
    *,
    receipts_root: Path | str | None = None,
    now: datetime | None = None,
) -> dict[str, ContactObservation]:
    """Build every contact's observation record; no provider is ever called."""

    root = Path(receipts_root) if receipts_root is not None else DEFAULT_RECEIPTS_ROOT
    current = now or datetime.now(timezone.utc)
    return {
        spec.contact_id: _build_record(spec, _latest_evidence(spec, root), now=current)
        for spec in _CONTACT_SPECS
    }


def contact_observations_report(
    *,
    receipts_root: Path | str | None = None,
    now: datetime | None = None,
) -> ContactObservationsReport:
    """The report embedded in UsageMonitor.health() and served read-only.

    Building it only reads local receipt files; absence of the receipts root
    is an honest all-not-observed report, never an error.
    """

    root = Path(receipts_root) if receipts_root is not None else DEFAULT_RECEIPTS_ROOT
    records = contact_observations(receipts_root=root, now=now)
    try:
        root_present = root.is_dir()
    except OSError:
        root_present = False
    return ContactObservationsReport(
        description=(
            "Passive per-contact observations from provider-dispatch receipts; "
            "not-observed and stale are honest states; no provider call is made."
        ),
        freshness_window_days=CONTACT_OBSERVATION_FRESHNESS.days,
        receipts_root=str(root),
        receipts_root_present=root_present,
        contacts=list(records.values()),
    )
