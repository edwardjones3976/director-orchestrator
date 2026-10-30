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

Contact isolation: every contact reads only receipt files its own shapes
match (its provider-lane directory and its filename prefixes), and a JSON
receipt that carries a ``provider`` value is used only when that value
matches the contact. Grok Bot (xAI family, Slack bot contact) has no
receipt-writing route at all (its dispatcher route writes screenshots under
the OS profile directory, never under the receipts root) and never reads
direct-Grok ``grok``/``xai`` receipts; ZCode has no dispatcher route and
never reads Z.ai API ``zai`` receipts; the Antigravity subscription CLI
never reads Gemini API ``gemini`` receipts. No record is ever labeled as
another provider's lane.

Real receipt layout (installed dispatcher, .claude/skills/provider-dispatch/
dispatch.py): ``provider_receipt`` lanes write ``receipts/<provider>/<stamp>
.json`` (zai, firecrawl) with a ``provider`` field; worktree runs write
``receipts/<worktree-name>/<provider-prefix>-<stamp>...`` files, where the
Antigravity CLI writes ``agy-<stamp>.receipt.json`` structured receipts with
NO ``provider`` field (identified by the ``agy-`` filename prefix plus their
structured fields) and Copilot writes only ``copilot-<stamp>.out.txt`` text
output (combined stdout+stderr regardless of exit status, so it can certify
no task outcome by itself). Stamps are ``YYYYMMDDTHHMMSSZ`` optionally
followed by ``-<microseconds>-<hex>``. Discovery walks the receipts root's
immediate subdirectories once, read-only, matching each contact's shapes.

Host-provider bridge: the bridge (host_provider_bridge.py) exposes no
local status record this reader may consult without network I/O, so
bridge-sourced aspects stay ``not-observed``; adding them needs a passive,
persisted bridge status file, which does not exist today.

ZCode family label: Foreman ruling 2026-10-29 10:25 PDT records
provider_family = "Z.ai" (Z.ai's own product). The ruling is now carried by
the contact_families table itself (zcode lane added with that label), so
this module resolves every family through the table and keeps no private
fallback.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .contact_families import provider_family_for
from .models import (
    ContactAuthState,
    ContactObservation,
    ContactObservationsReport,
    ContactObservationStaleness,
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

#: Explicit clock-skew allowance (Foreman decision on review finding 5,
#: 2026-10-30): a receipt stamped at most this far in the future is treated
#: as current, while anything further ahead is rejected as evidence outright
#: (never fresh, never selected over genuine receipts, so a future stamp can
#: never mask later genuine failures).
CONTACT_OBSERVATION_MAX_CLOCK_SKEW = timedelta(minutes=5)

#: An explicit status is either one of these (a success) or it loses to the
#: process exit code: FAILED_OR_AMBIGUOUS, error, failed, ATTEMPT_RESERVED
#: and every other non-success status text mark the attempt failed/ambiguous.
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
#: Installed dispatcher stamps: YYYYMMDDTHHMMSSZ optionally followed by
#: "-<microseconds>-<hex>" (utc_stamp, dispatch.py); older stamps are bare.
_STAMP_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z(?:-\d+-[0-9a-f]+)?$")


@dataclass(frozen=True)
class ReceiptShape:
    """One discoverable receipt file shape from the installed dispatcher.

    ``filename_prefix`` is matched at the start of the filename (empty means
    any name, used only inside a contact's own provider-lane directory) and
    ``filename_suffix`` at its end. ``kind`` names the parser: a JSON
    receipt carries structured outcome fields, while a text output file
    (combined stdout+stderr) can never certify an outcome by itself.
    """

    filename_prefix: str
    filename_suffix: str
    kind: str  # "json" or "text"


@dataclass(frozen=True)
class ContactSpec:
    """The static description of one observed contact.

    ``lane_directories`` are the contact's own provider-lane directories
    directly under the receipts root (``receipts/<provider>/``). Receipt
    shapes with a non-empty filename prefix are additionally discovered in
    every worktree directory (``receipts/<worktree-name>/``). A JSON receipt
    carrying a ``provider`` value is accepted only when that value is in
    ``receipt_providers``; a JSON receipt with no ``provider`` value is
    accepted only when its filename prefix identifies the contact
    (the Antigravity CLI writes its receipts that way). ``family_lane``
    resolves the provider-family label through contact_families; it has no
    fallback: every observed lane is in that table.
    """

    contact_id: str
    family_lane: str
    lane_directories: tuple[str, ...]
    receipt_providers: frozenset[str]
    receipt_shapes: tuple[ReceiptShape, ...]
    note: str


_CONTACT_SPECS: tuple[ContactSpec, ...] = (
    ContactSpec(
        contact_id="zai",
        family_lane="zai",
        lane_directories=("zai",),
        receipt_providers=frozenset({"zai"}),
        receipt_shapes=(ReceiptShape(filename_prefix="", filename_suffix=".json", kind="json"),),
        note="Z.ai (GLM) coding-plan API lane via provider-dispatch receipts. Quota is never inferred from plan presence.",
    ),
    ContactSpec(
        contact_id="github-copilot",
        family_lane="github-copilot",
        lane_directories=(),
        receipt_providers=frozenset({"github-copilot"}),
        receipt_shapes=(ReceiptShape(filename_prefix="copilot-", filename_suffix=".out.txt", kind="text"),),
        note=(
            "Observation only: issues-only specialist (AGENTS.md, owner direction "
            "2026-10-28); no execution, routing or producer capability. Copilot's "
            "dispatcher route writes text output only, which certifies no outcome, "
            "so this contact stays unconfirmed unless a structured receipt appears. "
            "Premium-request quota stays not-observed unless a receipt states it."
        ),
    ),
    ContactSpec(
        contact_id="firecrawl",
        family_lane="firecrawl",
        lane_directories=("firecrawl",),
        receipt_providers=frozenset({"firecrawl"}),
        receipt_shapes=(
            ReceiptShape(filename_prefix="", filename_suffix=".json", kind="json"),
            ReceiptShape(filename_prefix="", filename_suffix=".search.txt", kind="text"),
            ReceiptShape(filename_prefix="", filename_suffix=".scrape.txt", kind="text"),
            ReceiptShape(filename_prefix="", filename_suffix=".status.txt", kind="text"),
            ReceiptShape(filename_prefix="", filename_suffix=".developer.txt", kind="text"),
        ),
        note=(
            "Firecrawl research receipts: the structured JSON receipt carries the "
            "outcome; text output files are unconfirmed evidence. Content is never "
            "copied into the record."
        ),
    ),
    ContactSpec(
        contact_id="antigravity-subscription-cli",
        family_lane="antigravity",
        lane_directories=(),
        receipt_providers=frozenset({"antigravity", "agy", "antigravity-subscription-cli"}),
        receipt_shapes=(
            ReceiptShape(filename_prefix="agy-", filename_suffix=".receipt.json", kind="json"),
        ),
        note=(
            "Antigravity subscription CLI contact (Google family), identified by its "
            "agy- receipt filename prefix (the CLI writes no provider field). It is "
            "not the Gemini API lane and never reads gemini receipts."
        ),
    ),
    ContactSpec(
        contact_id="grok-bot",
        family_lane="grok-bot",
        lane_directories=(),
        receipt_providers=frozenset({"grok-bot"}),
        receipt_shapes=(),
        note=(
            "Grok Bot (xAI family) is a separate contact from direct Grok. Its "
            "dispatcher route writes no receipts under the receipts root (chat "
            "artifacts land in the OS profile directory, outside this reader), so "
            "it honestly stays not-observed; it never reads direct-Grok grok or "
            "xai receipts."
        ),
    ),
    ContactSpec(
        contact_id="zcode",
        family_lane="zcode",
        lane_directories=(),
        receipt_providers=frozenset({"zcode"}),
        receipt_shapes=(),
        note=(
            "ZCode (Z.ai family, contact_families zcode lane per Foreman ruling "
            "2026-10-29) has no dispatcher route yet, so no receipts exist for it; "
            "it is not the Z.ai API lane and never reads zai receipts."
        ),
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
    #: True when a dated attempt exists but this evidence cannot certify its
    #: outcome (text output, or a providerless zero-exit receipt).
    unconfirmed: bool = False


def _parse_stamp(value: Any) -> datetime | None:
    """Parse a ``YYYYMMDDTHHMMSSZ`` receipt stamp into an aware UTC datetime."""

    if not isinstance(value, str):
        return None
    match = _STAMP_RE.match(value.strip())
    if match is None:
        return None
    year, month, day, hour, minute, second = (int(part) for part in match.groups())
    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=UTC)
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


def _json_evidence(
    path: Path, spec: ContactSpec, *, identified_by_prefix: bool
) -> _ReceiptEvidence | None:
    """Read one JSON receipt passively; None when it is unusable or another contact's.

    ``identified_by_prefix`` says the filename itself carries this contact's
    prefix; that is the only way a receipt without a ``provider`` value is
    accepted (the Antigravity CLI writes receipts with no provider field).
    """

    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    provider = payload.get("provider")
    if isinstance(provider, str):
        if provider not in spec.receipt_providers:
            # Another contact's receipt is never evidence here, even in a
            # directory this contact otherwise reads.
            return None
    elif not identified_by_prefix:
        # A providerless JSON file without this contact's filename prefix
        # (for example a raw .out.json result dump) is not a receipt.
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
    status_text = status.strip().lower() if isinstance(status, str) else ""
    action = payload.get("action")
    action_text = action.strip().lower() if isinstance(action, str) else ""
    denied_commands = payload.get("denied_commands")
    denial_diagnostic = payload.get("denial_diagnostic")
    has_denials = bool(
        (isinstance(denied_commands, list) and denied_commands) or denial_diagnostic
    )

    # Failure precedence (Codex review finding 2, 2026-10-30): explicit
    # failure signals (is_error, non-zero exit code, non-success status, or
    # Antigravity command denials) take precedence over any success signal.
    # Success requires a success status (or, where no status field exists,
    # exit 0) AND no failure signal.
    if (
        is_error
        or (isinstance(exit_code, int) and exit_code != 0)
        or (status_text and status_text not in _SUCCESS_STATUSES)
        or has_denials
    ):
        task_success: bool | None = False
    elif status_text or (isinstance(exit_code, int) and exit_code == 0):
        # A success status exists (and no failure signal was present), or
        # no status field exists and exit 0 indicates success.
        task_success = True
    else:
        # A dated receipt with no task-outcome fields certifies no task result.
        task_success = None

    unconfirmed = False
    if task_success is True and (
        action_text == "status"
        or (spec.contact_id == "firecrawl" and not (isinstance(exit_code, int) and exit_code == 0))
    ):
        # Firecrawl action status check (Codex review finding 1, 2026-10-30):
        # a status-only check (--status) returns exit 0 even when unauthenticated,
        # so it never establishes authentication or readiness (at most observed-unconfirmed).
        # Only task actions exercising the service with exit 0 count as success.
        task_success = None
        unconfirmed = True
    elif task_success is True and not isinstance(provider, str):
        # A providerless receipt (the Antigravity CLI's agy-*.receipt.json)
        # records only the process exit code and denials; the producer also
        # fails runs with an empty response or denied actions, which this
        # receipt does not record. Its zero exit is therefore unconfirmed,
        # never a success: it can still certify a failure, never readiness.
        task_success = None
        unconfirmed = True
    failure_text = _stringify(
        payload,
        "error",
        "provider_error_code",
        "native_subtype",
        "status",
        "terminal_reason",
        "api_error_status",
        "denial_diagnostic",
    )
    return _ReceiptEvidence(
        path=path,
        observed_at=observed_at,
        transport=f"provider-dispatch receipt ({provider if isinstance(provider, str) else spec.contact_id})",
        task_success=task_success,
        auth_failed=task_success is False and any(marker in failure_text for marker in _AUTH_FAILURE_MARKERS),
        quota_limited=task_success is False and any(marker in failure_text for marker in _QUOTA_LIMIT_MARKERS),
        unconfirmed=unconfirmed,
    )


def _text_evidence(path: Path, spec: ContactSpec) -> _ReceiptEvidence | None:
    """Read one dated text output file (firecrawl .search/.scrape, Copilot
    .out.txt) passively.

    The dispatcher writes combined stdout and stderr to these files
    regardless of exit status, so a nonempty file certifies nothing about
    the task outcome: text-only evidence is at most observed-unconfirmed,
    never success and never ready (Codex review finding 3, 2026-10-30).
    The file's content is never opened or copied; only its dated name
    records that a dated attempt exists.
    """

    observed_at = _filename_stamp(path.name)
    if observed_at is None:
        return None
    kind = path.name.lower().removesuffix(".txt").rsplit(".", 1)[-1]
    return _ReceiptEvidence(
        path=path,
        observed_at=observed_at,
        transport=f"provider-dispatch text output ({spec.contact_id} {kind}, unconfirmed)",
        task_success=None,
        auth_failed=False,
        quota_limited=False,
        unconfirmed=True,
    )


def _match_shape(name: str, spec: ContactSpec) -> ReceiptShape | None:
    """The first of the contact's shapes a filename matches, if any."""

    lowered = name.lower()
    for shape in spec.receipt_shapes:
        if lowered.startswith(shape.filename_prefix) and lowered.endswith(shape.filename_suffix):
            return shape
    return None


def _evidence_rank(evidence: _ReceiptEvidence) -> tuple[datetime, bool, bool, str]:
    """Selection order: newest first, then certified outcomes, then structured over text, then a stable name."""

    return (
        evidence.observed_at,
        evidence.task_success is not None,
        evidence.path.suffix != ".txt",
        evidence.path.name,
    )


def _latest_evidence(
    spec: ContactSpec, receipts_root: Path, *, now: datetime
) -> _ReceiptEvidence | None:
    """The newest usable dated receipt for this contact, from its own shapes only.

    Discovery is bounded and read-only: the contact's own provider-lane
    directories directly under the receipts root, plus one non-recursive
    pass over every immediate subdirectory of the receipts root for this
    contact's prefixed receipt filenames (the installed dispatcher's
    ``receipts/<worktree-name>/`` layout). A receipt stamped further ahead
    than the clock-skew allowance is rejected outright and can never be
    selected over a genuine receipt.
    """

    latest: _ReceiptEvidence | None = None
    try:
        root_entries = list(receipts_root.iterdir())
    except OSError:
        root_entries = []
    search_dirs: list[tuple[Path, bool]] = []
    for directory in spec.lane_directories:
        search_dirs.append((receipts_root / directory, True))
    if any(shape.filename_prefix for shape in spec.receipt_shapes):
        # Only prefixed shapes live in worktree directories; contacts with
        # none (zai, firecrawl: provider-lane receipts only) skip this pass.
        for entry in root_entries:
            try:
                if entry.is_dir() and entry.name not in spec.lane_directories:
                    search_dirs.append((entry, False))
            except OSError:
                continue
    for directory, allow_unprefixed in search_dirs:
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.is_file():
                    continue
            except OSError:
                continue
            shape = _match_shape(entry.name, spec)
            if shape is None:
                continue
            if not shape.filename_prefix and not allow_unprefixed:
                # Unprefixed shapes are only read in the contact's own lane.
                continue
            if shape.kind == "json":
                evidence = _json_evidence(
                    entry, spec, identified_by_prefix=bool(shape.filename_prefix)
                )
            else:
                evidence = _text_evidence(entry, spec)
            if evidence is None:
                continue
            if evidence.observed_at > now + CONTACT_OBSERVATION_MAX_CLOCK_SKEW:
                # Future-dated beyond the explicit skew allowance: rejected as
                # evidence, never fresh, never masking genuine receipts.
                continue
            # Newest wins; on a tie (one Firecrawl call writes its JSON receipt
            # and its text output under one stamp) the outcome-bearing
            # structured receipt wins over unconfirmed text output.
            if latest is None or _evidence_rank(evidence) > _evidence_rank(latest):
                latest = evidence
    return latest


def _resolve_family(spec: ContactSpec) -> str:
    # Table-derived only: every observed lane (zcode included, Foreman
    # ruling 2026-10-29) is in contact_families, so an unknown lane is a
    # defect that fails closed rather than a label this module invents.
    return provider_family_for(spec.family_lane)


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
            if stale and (evidence.task_success is not None or evidence.unconfirmed)
            else ContactTaskResultState.OBSERVED_FAILURE
            if evidence.task_success is False
            else ContactTaskResultState.OBSERVED_SUCCESS
            if evidence.task_success is True
            else ContactTaskResultState.OBSERVED_UNCONFIRMED
            if evidence.unconfirmed
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
    current = now or datetime.now(UTC)
    return _build_record(spec, _latest_evidence(spec, root, now=current), now=current)


def contact_observations(
    *,
    receipts_root: Path | str | None = None,
    now: datetime | None = None,
) -> dict[str, ContactObservation]:
    """Build every contact's observation record; no provider is ever called."""

    root = Path(receipts_root) if receipts_root is not None else DEFAULT_RECEIPTS_ROOT
    current = now or datetime.now(UTC)
    return {
        spec.contact_id: _build_record(
            spec, _latest_evidence(spec, root, now=current), now=current
        )
        for spec in _CONTACT_SPECS
    }


def contact_observations_report(
    *,
    receipts_root: Path | str | None = None,
    now: datetime | None = None,
) -> ContactObservationsReport:
    """The full report served on the authenticated observations route.

    It carries receipt paths, so /health embeds only
    ``contact_observations_public_summary`` instead.

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


#: The only per-contact fields the unauthenticated /health payload carries:
#: identities, dated states and readiness. Paths (receipts root, receipt
#: source), transports and notes stay on the authenticated observations route.
PUBLIC_CONTACT_FIELDS = (
    "contact_id",
    "provider_family",
    "observed_at_utc",
    "auth_state",
    "quota_state",
    "permissions_state",
    "task_result_state",
    "staleness",
    "ready",
)


def contact_observations_public_summary(
    *,
    receipts_root: Path | str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The path-free summary embedded in UsageMonitor.health().

    /health is unauthenticated (Codex review finding 1, 2026-10-30), so this
    carries only per-contact states and counts: never the receipts root,
    never a receipt path, never receipt-derived free text.
    """

    report = contact_observations_report(receipts_root=receipts_root, now=now)
    contacts = [
        {field: value for field, value in record.model_dump(mode="json").items() if field in PUBLIC_CONTACT_FIELDS}
        for record in report.contacts
    ]
    counts = {"total": len(contacts), "ready": sum(1 for record in contacts if record["ready"])}
    for state in ContactObservationStaleness:
        counts[state.value] = sum(1 for record in contacts if record["staleness"] == state.value)
    return {
        "description": report.description,
        "freshness_window_days": report.freshness_window_days,
        "receipts_root_present": report.receipts_root_present,
        "counts": counts,
        "contacts": contacts,
    }
