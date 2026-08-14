from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .config import Settings
from .models import ProviderName, ProviderRun, UsageCheckRecord, UsageCheckStatus
from .providers import ProviderAdapter, provider_registry
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
    "prompted",
}
FAILED_STATUSES = {
    "ERROR",
    "CANCELLED",
    "EXPIRED",
    "failed",
    "cancelled",
    "incomplete",
    "requires_action",
}
SUPPORTED_PROVIDERS = {"openai", "cursor", "claude", "gemini", "perplexity"}
MINIMUM_INTERVAL = timedelta(minutes=15)


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
        self._lock = asyncio.Lock()

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

    def _is_due(self, provider: str, now: datetime, force: bool) -> bool:
        latest = self.store.latest_usage_check(provider)
        required_interval = MINIMUM_INTERVAL if force else self.interval
        return latest is None or now - latest.checked_at >= required_interval

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
            response = await client.post(
                "https://api.openai.com/v1/responses",
                headers=headers,
                json=body,
            )
            response.raise_for_status()
        payload = response.json()
        return ProviderRun(
            provider=ProviderName.OWNER,
            run_id=payload["id"],
            status=payload.get("status", "completed"),
            result=_output_text(payload),
            raw=payload,
        )

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

    async def _prompt(self, provider: str) -> ProviderRun:
        if provider == "openai":
            return await self._openai_usage_check()
        adapter = self.adapters[ProviderName(provider)]
        run = await adapter.usage_check(USAGE_CHECK_PROMPT)
        return await self._wait(adapter, run)

    def _record_from_run(self, cycle_id: str, provider: str, run: ProviderRun) -> UsageCheckRecord:
        raw_usage, input_tokens, output_tokens, total_tokens = normalize_usage(run.raw)
        failed = run.status in FAILED_STATUSES
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
            raw_usage=raw_usage,
            error=f"Provider ended with status {run.status}" if failed else None,
        )

    async def _post_summary(self, records: list[UsageCheckRecord]) -> None:
        if not records or not self.settings.slack_bot_token or not self.settings.slack_channel_id:
            return
        lines = ["*Aesop Director — periodic provider usage check*"]
        for record in records:
            usage = f"; tokens={record.total_tokens}" if record.total_tokens is not None else ""
            lines.append(f"• `{record.provider}`: {record.status.value}{usage}")
        lines.append("Model replies are availability signals; subscription balances remain authoritative only in provider dashboards.")
        headers = {
            "Authorization": f"Bearer {self.settings.slack_bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers=headers,
                    json={"channel": self.settings.slack_channel_id, "text": "\n".join(lines)},
                )
                response.raise_for_status()
                payload = response.json()
                if not payload.get("ok"):
                    raise RuntimeError(payload.get("error", "unknown_error"))
        except Exception:
            # A reporting failure must not turn successful provider checks into a retry storm.
            return

    async def run_once(self, force: bool = False) -> list[UsageCheckRecord]:
        if not self.settings.usage_check_enabled or not self.settings.allow_paid_runs:
            return []
        if self._lock.locked():
            return []
        async with self._lock:
            cycle_id = f"USAGE-{secrets.token_hex(5).upper()}"
            readiness = self.settings.usage_check_readiness()
            now = datetime.now(timezone.utc)
            records: list[UsageCheckRecord] = []
            providers = tuple(provider for provider in self.providers if provider in SUPPORTED_PROVIDERS)
            for index, provider in enumerate(providers):
                if not self._is_due(provider, now, force):
                    continue
                if not readiness.get(provider, False):
                    record = UsageCheckRecord(
                        cycle_id=cycle_id,
                        provider=provider,
                        status=UsageCheckStatus.SKIPPED,
                        error="Provider credential is not configured",
                    )
                else:
                    try:
                        run = await self._prompt(provider)
                        record = self._record_from_run(cycle_id, provider, run)
                    except Exception as exc:
                        record = UsageCheckRecord(
                            cycle_id=cycle_id,
                            provider=provider,
                            status=UsageCheckStatus.ERROR,
                            error=f"{type(exc).__name__}: {exc}"[:2_000],
                        )
                self.store.add_usage_check(record)
                records.append(record)
                if index < len(providers) - 1:
                    await asyncio.sleep(max(0, min(self.settings.usage_check_provider_delay_seconds, 60)))
            await self._post_summary(records)
            return records

    async def run_forever(self) -> None:
        if not self.settings.usage_check_enabled:
            return
        await asyncio.sleep(max(0, min(self.settings.usage_check_initial_delay_seconds, 3_600)))
        while True:
            await self.run_once()
            await asyncio.sleep(60)

    def health(self) -> dict[str, Any]:
        return {
            "enabled": self.settings.usage_check_enabled,
            "paid_run_gate_open": self.settings.allow_paid_runs,
            "interval_minutes": max(15, self.settings.usage_check_interval_minutes),
            "providers": list(self.providers),
            "unsupported_providers": list(self.unsupported_providers),
            "latest": [
                {
                    "provider": record.provider,
                    "status": record.status.value,
                    "checked_at": record.checked_at.isoformat(),
                    "total_tokens": record.total_tokens,
                }
                for record in self.store.latest_usage_checks()
            ],
        }
