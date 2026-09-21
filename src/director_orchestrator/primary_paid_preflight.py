# [[Claude Code | Claude Opus 5 | 2026-09-21 | 11:19 UTC | BEGIN]]
# [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 09:43 UTC | BEGIN]]
# [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 22:29 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-14 | 06:14 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 18:45 UTC | BEGIN]]
"""Read-only funding setup checks. Atomic attempt admission remains authoritative."""
from dataclasses import dataclass
import json
from datetime import timezone
from .antigravity_team import ProviderBudget, ProviderAttemptBudget, TeamAuthorizationError
from .entitlement_contracts import document_digest
from .primary_parallel import PaidSetupHold


@dataclass(frozen=True, slots=True)
class PlannedProviderAttempt:
    """Predicted binding only: not an admitted attempt or reservation receipt."""
    task_id: str
    assignment_id: str
    idempotency_key: str
    work_packet_digest: str
    writer_token_digest: str
    fencing_generation: int
    timeout_seconds: int
    budget_usd: float
    total_token_limit: int
    max_hops: int
    max_revisions: int
    allowed_tools: tuple[str, ...]
    allowed_external_effects: tuple[str, ...]
    # `time.monotonic()`-comparable; established once at readiness/admission
    # and carried unchanged into `ProviderAdapterContext.deadline`. None if no
    # upstream deadline could be established (legacy/offline caller).
    deadline: float | None = None


@dataclass(frozen=True, slots=True)
class ReadinessSelection:
    """The one immutable identity readiness checked, carried into admission.

    Not a timing value with a label: every field is something readiness
    actually verified and admission or pre-I/O can compare again. A changed
    packet, roster, signed budget/grant, enrollment document, writer
    generation or authority expiry produces a different selection, and a
    selection that cannot be produced again fails closed rather than letting
    admission re-resolve some other currently valid authority.
    """

    task_id: str
    assignment_id: str
    provider: str
    packet_digest: str
    roster: tuple[tuple[str, str], ...]
    attempt_ordinal: int
    authority_digest: str
    writer_fencing_generation: int
    deadline: float
    authority_day: str | None = None
    enrollment_digest: str | None = None

    @property
    def budget_evidence_digest(self) -> str:
        """The exact evidence digest atomic admission derives for this choice."""

        return "sha256:" + self.authority_digest

    def matches_admission(self, *, authority_digest: str) -> bool:
        return authority_digest == self.authority_digest


def _admit_spend_ceilings(*, connection, schema, budget, limits, wave_budget_usage):
    """Shared read-only ledger check for one signed budget across both lanes.

    Same conservative accounting inputs as admission; these reads do not
    promise capacity against concurrent spending. The transaction rechecks.
    """
    if connection.execute(f"SELECT 1 FROM {schema}.director_primary_provider_attempt a "
            f"LEFT JOIN {schema}.director_primary_provider_spend s USING(attempt_digest) "
            "WHERE s.attempt_digest IS NULL LIMIT 1").fetchone():
        raise PaidSetupHold()
    if connection.execute(f"SELECT 1 FROM {schema}.director_primary_provider_spend s "
            f"JOIN {schema}.director_primary_provider_usage u USING(attempt_digest) "
            "WHERE s.project_id=%s AND u.observed_tokens>s.token_limit LIMIT 1",
            (budget.project_id,)).fetchone():
        raise PaidSetupHold()
    totals = connection.execute(f"""SELECT
        COALESCE(sum(GREATEST(s.reserved_microusd,COALESCE(u.actual_microusd,0))) FILTER(WHERE s.task_id=%s),0) AS task_spend,
        COALESCE(sum(GREATEST(s.reserved_microusd,COALESCE(u.actual_microusd,0))) FILTER(WHERE s.budget_day=%s::date),0) AS daily_spend
        FROM {schema}.director_primary_provider_spend s
        LEFT JOIN {schema}.director_primary_provider_usage u USING(attempt_digest)
        WHERE s.project_id=%s""", (budget.task_id, budget.day, budget.project_id)).fetchone()
    amount = budget.attempt_reservation_microusd
    wave = wave_budget_usage if wave_budget_usage is not None else {}
    task_key = ("task", budget.project_id, budget.task_id)
    day_key = ("day", budget.project_id, budget.day)
    if (totals['task_spend'] + wave.get(task_key, 0) + amount > min(budget.task_limit_microusd, limits.task_ceiling_microusd)
            or totals['daily_spend'] + wave.get(day_key, 0) + amount > min(budget.daily_limit_microusd, limits.daily_ceiling_microusd)):
        raise PaidSetupHold()
    wave[task_key] = wave.get(task_key, 0) + amount
    wave[day_key] = wave.get(day_key, 0) + amount


def check_budget_setup(*, connection, routing, settings, provider, packet, attempt_usd, wave_budget_usage=None):
    authority = routing.authority
    day = authority.entitlements._clock().astimezone(timezone.utc).date().isoformat()
    rows = connection.execute(
        f"SELECT authority_digest, document FROM {authority.schema}.director_primary_provider_budget "
        "WHERE document->>'task_id'=%s AND document->>'provider_id'=%s "
        "AND document->>'project_id'=%s AND document->>'day'=%s",
        (packet['task_id'], provider, packet['project'], day)).fetchall()
    if len(rows) != 1:
        raise PaidSetupHold()
    budget = ProviderBudget.model_validate_json(json.dumps(rows[0]['document']))
    budget.verify(authority.entitlements._owner_authority_secret)
    if (budget.grant_digest != rows[0]['authority_digest'] or budget.day != day
            or budget.task_id != packet['task_id'] or budget.provider_id != provider
            or budget.project_id != packet['project']):
        raise PaidSetupHold()
    limits = ProviderAttemptBudget.from_usd(project_id=packet['project'],
        attempt_usd=attempt_usd, task_usd=settings.max_task_budget_usd,
        daily_usd=settings.max_daily_provider_budget_usd)
    limits.validate(budget)
    _admit_spend_ceilings(connection=connection, schema=authority.schema, budget=budget,
        limits=limits, wave_budget_usage=wave_budget_usage)
    return budget


def check_team_setup(*, connection, team_routing, settings, packet, attempt_usd, wave_budget_usage=None):
    """Pure signed-grant readiness check for one Antigravity team assignment.

    Loads and verifies the registered grant/budget pair the same way
    ``PrimaryTeamRouting.grant_for`` does, but on the caller's own bounded
    connection and without ``query_factory``, ``worker_factory``,
    ``TeamAuthority.authorize`` or ``admissions``: this is eligibility
    prediction, not the atomic entitlement admission that stays at dispatch.
    """
    authority = team_routing.authority
    rows = connection.execute(
        f"SELECT grant_digest FROM {authority.schema}.director_primary_team_grant "
        "WHERE document->>'task_id'=%s AND document->>'assignment_id'=%s AND NOT revoked",
        (packet['task_id'], packet['assignment_id'])).fetchall()
    if len(rows) != 1:
        raise PaidSetupHold()
    grant, budget = authority._load(connection, rows[0]['grant_digest'])
    if grant.packet_digest != document_digest(packet):
        raise PaidSetupHold()
    # A still-current team grant can be paired with a correctly signed but
    # stale (yesterday's) budget: TeamAuthority._load verifies the budget's
    # signature and its binding to the grant, never its UTC freshness. Without
    # this check, readiness would use yesterday's spend totals and pass, while
    # the actual monetary reserve later rejects the expired budget day -
    # defeating the early setup hold. Mirrors check_budget_setup's day check.
    if budget.day != authority.entitlements._clock().astimezone(timezone.utc).date().isoformat():
        raise PaidSetupHold()
    limits = ProviderAttemptBudget.from_usd(project_id=packet['project'],
        attempt_usd=attempt_usd, task_usd=settings.max_task_budget_usd,
        daily_usd=settings.max_daily_provider_budget_usd)
    limits.validate(budget)
    _admit_spend_ceilings(connection=connection, schema=authority.schema, budget=budget,
        limits=limits, wave_budget_usage=wave_budget_usage)
    return grant, budget
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 18:45 UTC | END]]

# [[OpenAI Codex | GPT-6 Astra | 2026-09-14 | 06:14 UTC | END]]
# [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 22:29 UTC | END]]
# [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 09:43 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 11:19 UTC | END]]
