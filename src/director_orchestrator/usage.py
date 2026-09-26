# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 10:23 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 04:14 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 08:02 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 03:47 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 03:38 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 00:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 22:11 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-21 | 23:40 UTC | BEGIN]]
from __future__ import annotations

import asyncio
import hashlib
import math
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from .config import Settings
from .contribution_tags import DIRECTOR_SYSTEM_LABEL, wrap_human_submission
from .models import (
    ProviderName,
    ProviderRun,
    UsageCheckRecord,
    UsageCheckRunResponse,
    UsageCheckRunStatus,
    UsageCheckStatus,
)
from .providers import (
    GeminiAdapter,
    ProviderAdapter,
    ProviderDailySpendLedger,
    ProviderError,
    provider_registry,
)
from .store import TaskStore


USAGE_CHECK_PROMPT = (
    "DIRECTOR_USAGE_CHECK. Reply with one short line confirming availability and the model or agent name. "
    "If this runtime directly exposes quota or usage information, include it; otherwise say QUOTA=UNAVAILABLE. "
    "Do not browse, call tools, inspect repositories, edit files, create branches, open pull requests, or perform project work."
)

TERMINAL_STATUSES = {
    "FINISHED",
    "ERROR",
    "CANCELLED",
    "EXPIRED",
    "completed",
    "failed",
    "cancelled",
    "incomplete",
    "requires_action",
    "budget_exceeded",
    "prompted",
}
SUCCESS_STATUSES = {"FINISHED", "completed", "prompted"}
SUPPORTED_PROVIDERS = {
    "openai",
    "cursor",
    "claude",
    "gemini",
    "grok",
    "perplexity",
}
MINIMUM_INTERVAL = timedelta(minutes=15)
MINIMUM_INTERVAL_MINUTES = 15


@dataclass(frozen=True)
class _ManualCycleReservation:
    cycle_id: str
    providers: tuple[str, ...]


def _output_text(payload: dict[str, Any]) -> str | None:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    chunks: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                chunks.append(content["text"])
    return "".join(chunks) or None


def _usage_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    for key, value in payload.items():
        normalized = key.replace("_", "").lower()
        if normalized in {"usage", "usagemetadata"} and isinstance(value, dict):
            return value
    for value in payload.values():
        found = _usage_payload(value)
        if found:
            return found
    return {}


def _token_value(usage: dict[str, Any], *names: str) -> int | None:
    normalized = {key.replace("_", "").lower(): value for key, value in usage.items()}
    for name in names:
        value = normalized.get(name.replace("_", "").lower())
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def normalize_usage(payload: dict[str, Any]) -> tuple[dict[str, Any], int | None, int | None, int | None]:
    usage = _usage_payload(payload)
    input_tokens = _token_value(usage, "input_tokens", "prompt_tokens", "prompt_token_count")
    output_tokens = _token_value(
        usage,
        "output_tokens",
        "completion_tokens",
        "candidates_token_count",
    )
    total_tokens = _token_value(usage, "total_tokens", "total_token_count")
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    return usage, input_tokens, output_tokens, total_tokens


def normalize_reported_cost(usage: dict[str, Any]) -> float | None:
    """Extract only a provider-reported USD total; never invent token pricing."""
    cost = usage.get("cost")
    if isinstance(cost, dict):
        currency = cost.get("currency")
        if isinstance(currency, str) and currency.upper() != "USD":
            return None
        cost = cost.get("total_cost")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        parsed = float(cost)
        if parsed >= 0 and math.isfinite(parsed):
            return parsed
    return None


class UsageMonitor:
    def __init__(
        self,
        settings: Settings,
        store: TaskStore,
        *,
        adapters: dict[ProviderName, ProviderAdapter] | None = None,
    ):
        self.settings = settings
        self.store = store
        self.adapters = adapters or provider_registry(settings)
        self.provider_spend = ProviderDailySpendLedger(store.path)
        self._lock = asyncio.Lock()
        self._schedule_lock = asyncio.Lock()
        self._reserved: _ManualCycleReservation | None = None

    @property
    def interval(self) -> timedelta:
        return max(
            MINIMUM_INTERVAL,
            timedelta(minutes=self.settings.usage_check_interval_minutes),
        )

    @property
    def providers(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.settings.usage_check_providers))

    @property
    def unsupported_providers(self) -> tuple[str, ...]:
        return tuple(provider for provider in self.providers if provider not in SUPPORTED_PROVIDERS)

    @property
    def supported_providers(self) -> tuple[str, ...]:
        return tuple(provider for provider in self.providers if provider in SUPPORTED_PROVIDERS)

    def _is_due(self, provider: str, now: datetime, force: bool) -> bool:
        latest = self.store.latest_usage_check(provider)
        required_interval = MINIMUM_INTERVAL if force else self.interval
        return latest is None or now - latest.checked_at >= required_interval

    @staticmethod
    def _utc_day_start(now: datetime) -> datetime:
        current = now.astimezone(timezone.utc)
        return current.replace(hour=0, minute=0, second=0, microsecond=0)

    def _daily_accounting(self, provider: str, now: datetime) -> dict[str, Any]:
        records = self.store.usage_checks_since(provider, self._utc_day_start(now))
        attempted = [
            record for record in records if record.status != UsageCheckStatus.SKIPPED
        ]
        request_count = sum(record.attempt_count for record in attempted)
        retry_count = sum(max(0, record.attempt_count - 1) for record in attempted)
        total_tokens = sum(record.total_tokens or 0 for record in attempted)
        reported_costs = [
            record.reported_cost_usd
            for record in attempted
            if record.reported_cost_usd is not None
        ]
        quota_codes = {
            "quota_exceeded",
            "rate_limit_exceeded",
            "resource_exhausted",
            "http_429",
        }
        quota_failures = sum(
            1 for record in attempted if record.provider_error_code in quota_codes
        )
        request_limit: int | None = None
        budget_usd: float | None = None
        if provider == "gemini":
            request_limit = self.settings.gemini_daily_request_limit
            budget_usd = self.settings.gemini_daily_budget_usd
        elif provider == "grok":
            request_limit = self.settings.grok_daily_request_limit
            budget_usd = self.settings.grok_daily_budget_usd
        elif provider == "perplexity":
            request_limit = self.settings.perplexity_daily_request_limit
            budget_usd = self.settings.perplexity_daily_budget_usd
        reserved_cost_usd: float | None = None
        if provider == "grok":
            request_count, reserved_cost_usd = self.provider_spend.totals(
                ProviderName.GROK, now=now
            )
        return {
            "request_count": request_count,
            # Kept apart (review of check 15): the monitor's own USAGE_CHECK_PROMPT probes, and real provider
            # (work) requests. request_count stays as it was for the limits above.
            "usage_check_count": sum(record.attempt_count for record in attempted),
            "provider_request_count": self._provider_request_count(provider, records, now),
            "retry_count": retry_count,
            "quota_failure_count": quota_failures,
            "total_tokens": total_tokens,
            "reported_cost_usd": round(sum(reported_costs), 8),
            "cost_visibility_complete": bool(attempted)
            and len(reported_costs) == len(attempted),
            "daily_request_limit": request_limit,
            "daily_budget_usd": budget_usd,
            "reserved_cost_usd": reserved_cost_usd,
        }

    def _provider_request_count(self, provider: str, records: list[UsageCheckRecord], now: datetime) -> int | None:
        """Real provider requests reserved today in the durable spend ledger, excluding this monitor's own probes
        (their effect key is sha256("usage:<cycle>:<provider>"), one per usage-check cycle). None for a provider
        the ledger does not account (its real requests are not visible to the monitor)."""
        try:
            name = ProviderName(provider)
        except ValueError:
            return None
        if name not in ProviderDailySpendLedger._SUPPORTED:
            return None
        probes = {hashlib.sha256(f"usage:{record.cycle_id}:{provider}".encode("utf-8")).hexdigest()
                  for record in records}
        return sum(key not in probes for key in self.provider_spend.effect_keys(name, now=now))

    def _supplemental_limit_error(self, provider: str, now: datetime) -> str | None:
        if provider not in {"gemini", "grok", "perplexity"}:
            return None
        accounting = self._daily_accounting(provider, now)
        request_limit = accounting["daily_request_limit"]
        if isinstance(request_limit, int) and accounting["request_count"] >= request_limit:
            return "Provider daily request limit reached"
        budget = accounting["daily_budget_usd"]
        accounted_cost = (
            accounting["reserved_cost_usd"]
            if accounting["reserved_cost_usd"] is not None
            else accounting["reported_cost_usd"]
        )
        if isinstance(budget, (int, float)) and accounted_cost >= budget:
            return "Provider daily cost limit reached"
        return None

    def _supplemental_remaining_requests(
        self, provider: str, now: datetime
    ) -> int | None:
        if provider not in {"gemini", "grok", "perplexity"}:
            return None
        accounting = self._daily_accounting(provider, now)
        request_limit = accounting["daily_request_limit"]
        if not isinstance(request_limit, int):
            return None
        return max(0, request_limit - accounting["request_count"])

    def partition_providers(
        self, *, force: bool, now: datetime | None = None
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Return (due, cooling_down) for configured supported providers."""
        current = now or datetime.now(timezone.utc)
        due: list[str] = []
        cooling_down: list[str] = []
        for provider in self.supported_providers:
            if self._is_due(provider, current, force):
                due.append(provider)
            else:
                cooling_down.append(provider)
        return tuple(due), tuple(cooling_down)

    def _clear_reservation(self, cycle_id: str | None) -> None:
        reserved = self._reserved
        if reserved is not None and cycle_id is not None and reserved.cycle_id == cycle_id:
            self._reserved = None

    async def reserve_manual_cycle(self, cycle_id: str) -> UsageCheckRunResponse:
        """Fail closed unless a forced cycle can actually schedule due providers.

        An accepted response reserves the cycle so a concurrent monitor pass cannot
        steal the lock and leave the caller with an empty cycle_id.
        """
        unsupported = list(self.unsupported_providers)
        if not self.settings.usage_check_enabled or not self.settings.allow_paid_runs:
            return UsageCheckRunResponse(
                status=UsageCheckRunStatus.BUSY,
                scheduled_providers=[],
                cooling_down_providers=[],
                unsupported_providers=unsupported,
                minimum_interval_minutes=MINIMUM_INTERVAL_MINUTES,
                reason="Usage checks or paid provider execution is disabled",
            )
        async with self._schedule_lock:
            if self._lock.locked() or self._reserved is not None:
                due, cooling_down = self.partition_providers(force=True)
                return UsageCheckRunResponse(
                    status=UsageCheckRunStatus.BUSY,
                    scheduled_providers=[],
                    cooling_down_providers=list(cooling_down),
                    unsupported_providers=unsupported,
                    minimum_interval_minutes=MINIMUM_INTERVAL_MINUTES,
                    reason="A usage-check cycle is already running or reserved",
                )
            due, cooling_down = self.partition_providers(force=True)
            if not due:
                return UsageCheckRunResponse(
                    status=UsageCheckRunStatus.NOT_DUE,
                    scheduled_providers=[],
                    cooling_down_providers=list(cooling_down),
                    unsupported_providers=unsupported,
                    minimum_interval_minutes=MINIMUM_INTERVAL_MINUTES,
                    reason=(
                        "All configured supported providers are within the "
                        f"{MINIMUM_INTERVAL_MINUTES}-minute minimum interval"
                    ),
                )
            self._reserved = _ManualCycleReservation(cycle_id=cycle_id, providers=due)
            return UsageCheckRunResponse(
                status=UsageCheckRunStatus.ACCEPTED,
                cycle_id=cycle_id,
                scheduled_providers=list(due),
                cooling_down_providers=list(cooling_down),
                unsupported_providers=unsupported,
                minimum_interval_minutes=MINIMUM_INTERVAL_MINUTES,
                reason=None,
            )

    @staticmethod
    def _openai_usage_retry_delay(response: httpx.Response) -> float | None:
        """Return a bounded delay for one unambiguously rejected request."""
        retry_after = response.headers.get("Retry-After")
        if retry_after is None:
            return 2.0
        try:
            delay = float(retry_after)
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                return None
        if not math.isfinite(delay) or not 0 <= delay <= 60:
            return None
        return delay

    async def _openai_usage_check(self) -> ProviderRun:
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.settings.openai_model,
            "input": USAGE_CHECK_PROMPT,
            "max_output_tokens": min(max(self.settings.usage_check_max_output_tokens, 16), 256),
            "store": False,
        }
        async with httpx.AsyncClient(timeout=180) as client:
            for attempt_count in range(1, 3):
                try:
                    response = await client.post(
                        "https://api.openai.com/v1/responses",
                        headers=headers,
                        json=body,
                    )
                except httpx.HTTPError as exc:
                    raise ProviderError(
                        "OpenAI usage check encountered a transport failure",
                        code="transport_error",
                        attempt_count=attempt_count,
                    ) from exc
                if response.status_code == 429:
                    retry_delay = self._openai_usage_retry_delay(response)
                    if attempt_count == 2 or retry_delay is None:
                        raise ProviderError(
                            "OpenAI usage check received HTTP 429 without a safe recovery window",
                            code="http_429",
                            attempt_count=attempt_count,
                        )
                    await asyncio.sleep(retry_delay)
                    continue
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise ProviderError(
                        f"OpenAI usage check returned HTTP {response.status_code}",
                        code=f"http_{response.status_code}",
                        attempt_count=attempt_count,
                    ) from exc
                try:
                    payload = response.json()
                    return ProviderRun(
                        provider=ProviderName.OPENAI,
                        run_id=payload["id"],
                        status=payload.get("status", "completed"),
                        result=_output_text(payload),
                        raw=payload,
                        attempt_count=attempt_count,
                    )
                except (ValueError, KeyError, TypeError, AttributeError) as exc:
                    raise ProviderError(
                        "OpenAI usage check returned an invalid response",
                        code="invalid_response",
                        attempt_count=attempt_count,
                    ) from exc
        raise AssertionError("OpenAI usage recovery loop exited unexpectedly")

    async def _wait(self, adapter: ProviderAdapter, run: ProviderRun) -> ProviderRun:
        if run.status in TERMINAL_STATUSES:
            return run
        deadline = asyncio.get_running_loop().time() + max(
            30, min(self.settings.usage_check_timeout_seconds, 1_800)
        )
        current = run
        while current.status not in TERMINAL_STATUSES:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(f"{current.provider.value} usage check timed out")
            await asyncio.sleep(max(1, self.settings.provider_poll_seconds))
            current = await adapter.poll(current)
        return current

    async def _prompt(
        self, provider: str, *, remaining_requests: int | None = None
    ) -> ProviderRun:
        if provider == "openai":
            return await self._openai_usage_check()
        adapter = self.adapters[ProviderName(provider)]
        if provider == "gemini" and isinstance(adapter, GeminiAdapter):
            run = await adapter.usage_check(
                USAGE_CHECK_PROMPT,
                max_attempts=remaining_requests,
            )
        else:
            run = await adapter.usage_check(USAGE_CHECK_PROMPT)
        return await self._wait(adapter, run)

    def _record_from_run(self, cycle_id: str, provider: str, run: ProviderRun) -> UsageCheckRecord:
        raw_usage, input_tokens, output_tokens, total_tokens = normalize_usage(run.raw)
        failed = run.status not in SUCCESS_STATUSES
        status = UsageCheckStatus.ERROR if failed else (
            UsageCheckStatus.PROMPTED if run.status == "prompted" else UsageCheckStatus.OK
        )
        return UsageCheckRecord(
            cycle_id=cycle_id,
            provider=provider,
            status=status,
            response_text=(run.result or "")[:2_000] or None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            reported_cost_usd=normalize_reported_cost(raw_usage),
            attempt_count=run.attempt_count,
            raw_usage=raw_usage,
            error=f"Provider ended with status {run.status}" if failed else None,
        )

    async def _post_summary(self, records: list[UsageCheckRecord]) -> None:
        if not records or not self.settings.slack_bot_token or not self.settings.slack_channel_id:
            return
        lines = ["*Aesop Director — periodic provider usage check*"]
        for record in records:
            usage = f"; tokens={record.total_tokens}" if record.total_tokens is not None else ""
            cost = (
                f"; reported_cost_usd={record.reported_cost_usd:.6f}"
                if record.reported_cost_usd is not None
                else ""
            )
            retries = f"; attempts={record.attempt_count}" if record.attempt_count > 1 else ""
            pending = "; completion=pending_callback" if record.status == UsageCheckStatus.PROMPTED else ""
            lines.append(
                f"• `{record.provider}`: {record.status.value}{usage}{cost}{retries}{pending}"
            )
        lines.append("Model replies are availability signals; subscription balances remain authoritative only in provider dashboards.")
        headers = {
            "Authorization": f"Bearer {self.settings.slack_bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        tagged_summary = wrap_human_submission(
            "\n".join(lines),
            "Aesop Director",
            DIRECTOR_SYSTEM_LABEL,
            at=max(record.checked_at for record in records),
            slack_markup=True,
            neutralize_embedded_tags=True,
        )
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers=headers,
                    json={"channel": self.settings.slack_channel_id, "text": tagged_summary},
                )
                response.raise_for_status()
                payload = response.json()
                if not payload.get("ok"):
                    raise RuntimeError(payload.get("error", "unknown_error"))
        except Exception:
            # A reporting failure must not turn successful provider checks into a retry storm.
            return

    async def run_once(
        self, force: bool = False, cycle_id: str | None = None
    ) -> list[UsageCheckRecord]:
        if not self.settings.usage_check_enabled or not self.settings.allow_paid_runs:
            async with self._schedule_lock:
                self._clear_reservation(cycle_id)
            return []

        async with self._schedule_lock:
            reserved = self._reserved
            if reserved is not None and (not cycle_id or reserved.cycle_id != cycle_id):
                return []
            is_reserved_owner = (
                reserved is not None and cycle_id is not None and reserved.cycle_id == cycle_id
            )

        # Periodic passes skip when another cycle holds the lock. A reserved manual
        # cycle waits instead of returning empty after the API already accepted it.
        if not is_reserved_owner and self._lock.locked():
            return []

        async with self._lock:
            try:
                async with self._schedule_lock:
                    reserved = self._reserved
                    if reserved is not None:
                        if not cycle_id or reserved.cycle_id != cycle_id:
                            return []
                        provider_filter = set(reserved.providers)
                        self._reserved = None
                    else:
                        provider_filter = None

                cycle_id = cycle_id or f"USAGE-{secrets.token_hex(5).upper()}"
                readiness = self.settings.usage_check_readiness()
                now = datetime.now(timezone.utc)
                records: list[UsageCheckRecord] = []
                providers = self.supported_providers
                for index, provider in enumerate(providers):
                    if provider_filter is not None and provider not in provider_filter:
                        continue
                    if not self._is_due(provider, now, force):
                        continue
                    limit_error = self._supplemental_limit_error(provider, now)
                    if not readiness.get(provider, False):
                        record = UsageCheckRecord(
                            cycle_id=cycle_id,
                            provider=provider,
                            status=UsageCheckStatus.SKIPPED,
                            attempt_count=0,
                            error="Provider credential is not configured",
                        )
                    elif limit_error:
                        record = UsageCheckRecord(
                            cycle_id=cycle_id,
                            provider=provider,
                            status=UsageCheckStatus.SKIPPED,
                            attempt_count=0,
                            error=limit_error,
                        )
                    else:
                        try:
                            if provider == "grok":
                                effect_key = hashlib.sha256(
                                    f"usage:{cycle_id}:{provider}".encode("utf-8")
                                ).hexdigest()
                                self.provider_spend.reserve(
                                    provider=ProviderName.GROK,
                                    effect_key=effect_key,
                                    estimated_cost_usd=self.settings.grok_request_cost_floor_usd,
                                    request_limit=self.settings.grok_daily_request_limit,
                                    budget_usd=self.settings.grok_daily_budget_usd,
                                    now=now,
                                )
                            run = await self._prompt(
                                provider,
                                remaining_requests=self._supplemental_remaining_requests(
                                    provider, now
                                ),
                            )
                            record = self._record_from_run(cycle_id, provider, run)
                        except Exception as exc:
                            record = UsageCheckRecord(
                                cycle_id=cycle_id,
                                provider=provider,
                                status=UsageCheckStatus.ERROR,
                                attempt_count=max(
                                    1, min(int(getattr(exc, "attempt_count", 1)), 10)
                                ),
                                provider_error_code=(
                                    str(getattr(exc, "code"))[:100]
                                    if getattr(exc, "code", None)
                                    else None
                                ),
                                error=f"{type(exc).__name__}: {exc}"[:2_000],
                            )
                    self.store.add_usage_check(record)
                    records.append(record)
                    if index < len(providers) - 1:
                        await asyncio.sleep(
                            max(0, min(self.settings.usage_check_provider_delay_seconds, 60))
                        )
                await self._post_summary(records)
                return records
            finally:
                async with self._schedule_lock:
                    self._clear_reservation(cycle_id)

    async def run_forever(self) -> None:
        if not self.settings.usage_check_enabled:
            return
        await asyncio.sleep(max(0, min(self.settings.usage_check_initial_delay_seconds, 3_600)))
        while True:
            await self.run_once()
            await asyncio.sleep(60)

    def health(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        latest_records = self.store.latest_usage_checks()
        latest_by_provider = {record.provider: record for record in latest_records}
        current_signals: list[str] = []
        completion_verified: list[str] = []
        pending_completion: list[str] = []
        latest: list[dict[str, Any]] = []
        for record in latest_records:
            fresh = now - record.checked_at <= self.interval
            signal_current = fresh and record.status in {
                UsageCheckStatus.OK,
                UsageCheckStatus.PROMPTED,
            }
            if signal_current:
                current_signals.append(record.provider)
            if fresh and record.status == UsageCheckStatus.OK:
                completion_verified.append(record.provider)
            if fresh and record.status == UsageCheckStatus.PROMPTED:
                pending_completion.append(record.provider)
            latest.append(
                {
                    "provider": record.provider,
                    "status": record.status.value,
                    "checked_at": record.checked_at.isoformat(),
                    "fresh": fresh,
                    "completion_verified": fresh
                    and record.status == UsageCheckStatus.OK,
                    "total_tokens": record.total_tokens,
                    "reported_cost_usd": record.reported_cost_usd,
                    "attempt_count": record.attempt_count,
                    "provider_error_code": record.provider_error_code,
                }
            )
        configured = list(self.supported_providers)
        return {
            "enabled": self.settings.usage_check_enabled,
            "paid_run_gate_open": self.settings.allow_paid_runs,
            "gate_state": (
                "open"
                if self.settings.usage_check_enabled and self.settings.allow_paid_runs
                else "usage_disabled"
                if not self.settings.usage_check_enabled
                else "paid_runs_disabled"
            ),
            "interval_minutes": max(15, self.settings.usage_check_interval_minutes),
            "providers": list(self.providers),
            "unsupported_providers": list(self.unsupported_providers),
            "all_configured_have_recent_signal": bool(configured)
            and not self.unsupported_providers
            and all(provider in current_signals for provider in configured),
            "all_configured_completion_verified": bool(configured)
            and not self.unsupported_providers
            and all(provider in completion_verified for provider in configured),
            "completion_verified_providers": completion_verified,
            "pending_completion_providers": pending_completion,
            "missing_or_stale_providers": [
                provider
                for provider in configured
                if provider not in current_signals
                or provider not in latest_by_provider
            ],
            # The UTC day every daily_accounting entry covers, from the same `now`, so a
            # reader never has to guess it from its own clock across midnight.
            "accounting_day": self._utc_day_start(now).date().isoformat(),
            "daily_accounting": {
                provider: self._daily_accounting(provider, now)
                for provider in configured
            },
            "latest": latest,
        }
# [[Codex | GPT-5.6 | 2026-08-21 | 23:40 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 22:11 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 00:25 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 03:38 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 03:47 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 08:02 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 04:14 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 10:23 UTC | END]]
