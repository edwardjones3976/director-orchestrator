# [[OpenAI Codex | GPT-6 Astra | 2026-09-14 | 06:14 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 18:45 UTC | BEGIN]]
"""Read-only funding setup checks. Atomic attempt admission remains authoritative."""
from dataclasses import dataclass
import json
from datetime import timezone
from .antigravity_team import ProviderBudget, ProviderAttemptBudget, TeamAuthorizationError
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
    # Same conservative accounting inputs as admission; these reads do not
    # promise capacity against concurrent spending. The transaction rechecks.
    if connection.execute(f"SELECT 1 FROM {authority.schema}.director_primary_provider_attempt a "
            f"LEFT JOIN {authority.schema}.director_primary_provider_spend s USING(attempt_digest) "
            "WHERE s.attempt_digest IS NULL LIMIT 1").fetchone():
        raise PaidSetupHold()
    if connection.execute(f"SELECT 1 FROM {authority.schema}.director_primary_provider_spend s "
            f"JOIN {authority.schema}.director_primary_provider_usage u USING(attempt_digest) "
            "WHERE s.project_id=%s AND u.observed_tokens>s.token_limit LIMIT 1",
            (budget.project_id,)).fetchone():
        raise PaidSetupHold()
    totals = connection.execute(f"""SELECT
        COALESCE(sum(GREATEST(s.reserved_microusd,COALESCE(u.actual_microusd,0))) FILTER(WHERE s.task_id=%s),0) AS task_spend,
        COALESCE(sum(GREATEST(s.reserved_microusd,COALESCE(u.actual_microusd,0))) FILTER(WHERE s.budget_day=%s::date),0) AS daily_spend
        FROM {authority.schema}.director_primary_provider_spend s
        LEFT JOIN {authority.schema}.director_primary_provider_usage u USING(attempt_digest)
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
    return budget
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 18:45 UTC | END]]

# [[OpenAI Codex | GPT-6 Astra | 2026-09-14 | 06:14 UTC | END]]
