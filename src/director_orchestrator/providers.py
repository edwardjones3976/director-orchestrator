# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 00:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 22:11 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 01:59 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-24 | 23:46 UTC | BEGIN]]
from __future__ import annotations

import asyncio
import json
import re
import secrets
import sqlite3
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import httpx

from .antigravity import (
    NON_AUTHORITATIVE_NOTICE,
    AntigravityContractError,
    antigravity_wire_input,
)
from .config import Settings
from .models import Assignment, AssignmentKind, DiscussionPhase, ProviderName, ProviderRun
from .policy import antigravity_assignment_errors, canonical_repository

_CURSOR_WORKING_REF_PREFIX = "cursor/"
_MISSING_STARTING_REF_STATUSES = {400, 404, 409, 422}


class ProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        attempt_count: int = 1,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.attempt_count = attempt_count


class PostSubmitAmbiguityError(ProviderError):
    """A provider accepted a create request, but its new run cannot be identified."""


class ProviderDailyLimitExceeded(ProviderError):
    """A durable provider-wide request or estimated-spend ceiling is exhausted."""


class ProviderReservationIdentityConflict(ProviderError):
    """A durable effect key was replayed with contradictory accounting identity."""


class ProviderDailySpendLedger:
    """Reserve daily supplemental-provider requests before paid submission.

    Reservations are conservative and idempotent by external-effect key. They are
    never released after a failed or ambiguous call because the provider may have
    accepted work and because under-counting is less safe than over-counting.
    """

    _EFFECT_KEY = re.compile(r"^[0-9a-f]{64}$")
    _SUPPORTED = {ProviderName.GEMINI, ProviderName.GROK, ProviderName.PERPLEXITY}

    def __init__(self, sqlite_path: str):
        self.path = sqlite_path
        Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS provider_daily_spend_reservations (
                    provider TEXT NOT NULL,
                    effect_key TEXT NOT NULL,
                    utc_day TEXT NOT NULL,
                    estimated_cost_usd REAL NOT NULL CHECK(estimated_cost_usd >= 0),
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY(provider, effect_key)
                )
                """
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_provider_daily_spend_day
                ON provider_daily_spend_reservations(provider, utc_day)"""
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA secure_delete = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def reserve(
        self,
        *,
        provider: ProviderName,
        effect_key: str,
        estimated_cost_usd: float,
        request_limit: int,
        budget_usd: float,
        now: datetime | None = None,
    ) -> tuple[int, float]:
        if provider not in self._SUPPORTED:
            raise ValueError(
                "daily assignment spend applies only to Gemini, Grok, and Perplexity"
            )
        if not self._EFFECT_KEY.fullmatch(effect_key):
            raise ValueError("provider spend reservation requires a canonical effect key")
        if request_limit < 1 or budget_usd <= 0 or estimated_cost_usd < 0:
            raise ValueError("provider daily ceilings must be positive")
        observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        utc_day = observed_at.date().isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """SELECT utc_day, estimated_cost_usd
                FROM provider_daily_spend_reservations
                WHERE provider = ? AND effect_key = ?""",
                (provider.value, effect_key),
            ).fetchone()
            if existing is not None:
                if existing[0] != utc_day or abs(float(existing[1]) - estimated_cost_usd) > 1e-9:
                    raise ProviderReservationIdentityConflict(
                        "provider daily reservation identity was reused with different accounting"
                    )
            else:
                count, reserved = connection.execute(
                    """SELECT COUNT(*), COALESCE(SUM(estimated_cost_usd), 0)
                    FROM provider_daily_spend_reservations
                    WHERE provider = ? AND utc_day = ?""",
                    (provider.value, utc_day),
                ).fetchone()
                if int(count) + 1 > request_limit:
                    raise ProviderDailyLimitExceeded(
                        f"{provider.value} daily assignment request limit reached"
                    )
                if float(reserved) + estimated_cost_usd > budget_usd + 1e-9:
                    raise ProviderDailyLimitExceeded(
                        f"{provider.value} daily assignment budget reached"
                    )
                connection.execute(
                    """INSERT INTO provider_daily_spend_reservations(
                        provider, effect_key, utc_day, estimated_cost_usd, recorded_at
                    ) VALUES (?, ?, ?, ?, ?)""",
                    (
                        provider.value,
                        effect_key,
                        utc_day,
                        estimated_cost_usd,
                        observed_at.isoformat(),
                    ),
                )
            count, reserved = connection.execute(
                """SELECT COUNT(*), COALESCE(SUM(estimated_cost_usd), 0)
                FROM provider_daily_spend_reservations
                WHERE provider = ? AND utc_day = ?""",
                (provider.value, utc_day),
            ).fetchone()
            return int(count), float(reserved)

    def totals(
        self,
        provider: ProviderName,
        *,
        now: datetime | None = None,
    ) -> tuple[int, float]:
        """Return the single durable accounting total shared by work and probes."""

        if provider not in self._SUPPORTED:
            raise ValueError("daily spend totals apply only to supplemental providers")
        observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        with self._connect() as connection:
            count, reserved = connection.execute(
                """SELECT COUNT(*), COALESCE(SUM(estimated_cost_usd), 0)
                FROM provider_daily_spend_reservations
                WHERE provider = ? AND utc_day = ?""",
                (provider.value, observed_at.date().isoformat()),
            ).fetchone()
        return int(count), float(reserved)


def _response_error_blob(response: httpx.Response) -> str:
    parts: list[str] = []
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError, TypeError):
        payload = None
    if payload is not None:
        try:
            parts.append(json.dumps(payload, ensure_ascii=False))
        except (TypeError, ValueError):
            parts.append(str(payload))
    text = getattr(response, "text", None)
    if isinstance(text, str) and text:
        parts.append(text)
    return "\n".join(parts).lower()


def _is_missing_starting_ref_error(response: httpx.Response) -> bool:
    """True only for a pre-creation missing GitHub starting ref."""
    if response.status_code not in _MISSING_STARTING_REF_STATUSES:
        return False
    blob = _response_error_blob(response)
    return "github branch not found" in blob or (
        "branch" in blob
        and "does not exist" in blob
        and "deleted after its pull request merged" in blob
    )


def _is_cursor_working_ref(ref: str) -> bool:
    return ref.startswith(_CURSOR_WORKING_REF_PREFIX)


class ProviderAdapter:
    name: ProviderName

    def __init__(self, settings: Settings):
        self.settings = settings

    async def dispatch(self, assignment: Assignment, context: str) -> ProviderRun:
        raise NotImplementedError

    async def poll(self, run: ProviderRun) -> ProviderRun:
        raise NotImplementedError

    async def revise(
        self,
        run: ProviderRun,
        prompt: str,
        *,
        assignment: Assignment | None = None,
        context: str | None = None,
    ) -> ProviderRun:
        raise ProviderError(f"{self.name} follow-up is not configured")

    async def usage_check(self, prompt: str) -> ProviderRun:
        raise ProviderError(f"{self.name} usage check is not configured")


class CursorAdapter(ProviderAdapter):
    name = ProviderName.CURSOR

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.settings.cursor_api_key}", "Content-Type": "application/json"}

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float | None:
        """Return a bounded delay only for an explicit, pre-creation rate limit."""
        if response.status_code != 429:
            return None
        retry_after: object = response.headers.get("Retry-After")
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict):
            for detail in payload.get("details", []):
                if not isinstance(detail, dict):
                    continue
                debug = detail.get("debug")
                provider_details = debug.get("details") if isinstance(debug, dict) else None
                if not isinstance(provider_details, dict) or not provider_details.get("isRetryable"):
                    continue
                additional = provider_details.get("additionalInfo")
                if isinstance(additional, dict) and additional.get("retryAfter") is not None:
                    retry_after = additional["retryAfter"]
                    break
        try:
            parsed = float(retry_after) if retry_after is not None else float(2**attempt)
        except (TypeError, ValueError):
            parsed = float(2**attempt)
        return min(
            max(parsed, 0.0),
            float(max(0, self.settings.cursor_dispatch_max_retry_delay_seconds)),
        )

    def _body_starting_ref(self, body: dict[str, Any]) -> str | None:
        repos = body.get("repos")
        if not isinstance(repos, list) or not repos or not isinstance(repos[0], dict):
            return None
        ref = repos[0].get("startingRef")
        return ref if isinstance(ref, str) and ref else None

    def _fallback_starting_ref(self, requested: str | None) -> str | None:
        if not requested or not _is_cursor_working_ref(requested):
            return None
        allowed = [item for item in self.settings.allowed_starting_refs if item]
        if not allowed:
            return None
        default = allowed[0]
        if requested == default:
            return None
        return default

    def _apply_starting_ref_fallback(self, body: dict[str, Any]) -> bool:
        requested = self._body_starting_ref(body)
        fallback = self._fallback_starting_ref(requested)
        if fallback is None:
            return False
        repos = body.get("repos")
        if not isinstance(repos, list) or not repos or not isinstance(repos[0], dict):
            return False
        repos[0]["startingRef"] = fallback
        return True

    def _dispatch_body(self, assignment: Assignment, context: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": f"Director task {assignment.sequence}",
            "prompt": {
                "text": (
                    f"{assignment.task}\n\nAcceptance checks:\n- "
                    + "\n- ".join(assignment.acceptance)
                    + f"\n\nContext:\n{context}"
                )
            },
            "workOnCurrentBranch": False,
            "autoCreatePR": assignment.allow_draft_pr,
            "skipReviewerRequest": False,
        }
        if assignment.repository:
            repository_url = assignment.repository.strip().rstrip("/")
            if "://" not in repository_url:
                repository_url = f"https://github.com/{repository_url.removesuffix('.git')}"
            body["repos"] = [{"url": repository_url, "startingRef": assignment.starting_ref}]
        return body

    async def dispatch(self, assignment: Assignment, context: str) -> ProviderRun:
        body = self._dispatch_body(assignment, context)
        attempts = max(1, self.settings.cursor_dispatch_max_attempts)
        fallback_used = False
        attempt = 0
        while True:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    f"{self.settings.cursor_api_base}/v1/agents",
                    headers=self.headers,
                    json=deepcopy(body),
                )
            if response.status_code < 400:
                break
            if (
                not fallback_used
                and _is_missing_starting_ref_error(response)
                and self._apply_starting_ref_fallback(body)
            ):
                fallback_used = True
                continue
            delay = self._retry_delay(response, attempt)
            if delay is None or attempt + 1 >= attempts:
                response.raise_for_status()
            await asyncio.sleep(delay)
            attempt += 1
        payload = response.json()
        return ProviderRun(
            provider=self.name,
            run_id=payload["run"]["id"],
            session_id=payload["agent"]["id"],
            status=payload["run"]["status"],
            url=payload["agent"].get("url"),
            raw=payload,
        )

    async def poll(self, run: ProviderRun) -> ProviderRun:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(
                f"{self.settings.cursor_api_base}/v1/agents/{run.session_id}/runs/{run.run_id}",
                headers=self.headers,
            )
            response.raise_for_status()
        payload = response.json()
        return run.model_copy(update={"status": payload["status"], "result": payload.get("result"), "raw": payload})

    async def revise(
        self,
        run: ProviderRun,
        prompt: str,
        *,
        assignment: Assignment | None = None,
        context: str | None = None,
    ) -> ProviderRun:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.settings.cursor_api_base}/v1/agents/{run.session_id}/runs",
                headers=self.headers,
                json={"prompt": {"text": prompt}},
            )
        if response.status_code < 400:
            payload = response.json()["run"]
            return ProviderRun(
                provider=self.name,
                run_id=payload["id"],
                session_id=run.session_id,
                status=payload["status"],
                url=run.url,
                raw=payload,
            )
        if _is_missing_starting_ref_error(response):
            if assignment is None:
                raise ProviderError(
                    "Cursor follow-up failed because its working branch is gone; "
                    "a fresh dispatch requires the bounded assignment"
                )
            fallback = self._fallback_starting_ref(assignment.starting_ref)
            revised = assignment.model_copy(
                update={
                    "task": (
                        f"{assignment.task}\n\nRevision required after the prior "
                        f"Cursor working branch was deleted:\n{prompt}"
                    ),
                    "starting_ref": fallback or assignment.starting_ref,
                }
            )
            return await self.dispatch(revised, context or "")
        response.raise_for_status()
        raise ProviderError("Cursor follow-up returned an empty success payload")

    async def usage_check(self, prompt: str) -> ProviderRun:
        # An authenticated read, not an agent launch. Verified live 2026-08-14:
        # GET /v1/models answers 200 in ~0.5s with this credential, while
        # POST /v1/agents hangs past 60s regardless of body and would burn a
        # paid cloud agent per probe even when it works. Availability only
        # needs proof the credential is accepted.
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{self.settings.cursor_api_base}/v1/models",
                headers=self.headers,
            )
            response.raise_for_status()
        payload = response.json()
        models = payload.get("items", [])
        return ProviderRun(
            provider=self.name,
            run_id="usage-check-models",
            status="FINISHED",
            result=f"Cursor API credential accepted; {len(models)} models listed. QUOTA=UNAVAILABLE",
            raw=payload,
        )


class ClaudeAdapter(ProviderAdapter):
    name = ProviderName.CLAUDE

    async def dispatch(self, assignment: Assignment, context: str) -> ProviderRun:
        headers = {
            "Authorization": f"Bearer {self.settings.claude_routine_token}",
            "anthropic-beta": self.settings.claude_routine_beta,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        text = json.dumps({
            "assignment": assignment.model_dump(mode="json"),
            "context": context,
            "completion_requirement": (
                "Return the bounded completion report for authenticated callback delivery; "
                "the orchestrator is the only Slack writer."
            ),
        })
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(self.settings.claude_routine_url, headers=headers, json={"text": text})
            response.raise_for_status()
        payload = response.json()
        return ProviderRun(
            provider=self.name,
            run_id=payload["claude_code_session_id"],
            session_id=payload["claude_code_session_id"],
            status="WAITING_CALLBACK",
            url=payload.get("claude_code_session_url"),
            raw=payload,
        )

    async def poll(self, run: ProviderRun) -> ProviderRun:
        return run

    async def revise(
        self,
        run: ProviderRun,
        prompt: str,
        *,
        assignment: Assignment | None = None,
        context: str | None = None,
    ) -> ProviderRun:
        if assignment is None or context is None:
            raise ProviderError("claude revision requires the bounded assignment and approval context")
        headers = {
            "Authorization": f"Bearer {self.settings.claude_routine_token}",
            "anthropic-beta": self.settings.claude_routine_beta,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        text = json.dumps(
            {
                "assignment": assignment.model_dump(mode="json"),
                "revision": {
                    "prior_run_id": run.run_id,
                    "prompt": prompt,
                },
                "context": context,
                "completion_requirement": (
                    "Return the bounded revision report for authenticated callback delivery; "
                    "the orchestrator is the only Slack writer."
                ),
            }
        )
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                self.settings.claude_routine_url,
                headers=headers,
                json={"text": text},
            )
            response.raise_for_status()
        payload = response.json()
        session_id = payload["claude_code_session_id"]
        return ProviderRun(
            provider=self.name,
            run_id=session_id,
            session_id=session_id,
            status="WAITING_CALLBACK",
            url=payload.get("claude_code_session_url"),
            raw=payload,
        )

    async def usage_check(self, prompt: str) -> ProviderRun:
        headers = {
            "Authorization": f"Bearer {self.settings.claude_routine_token}",
            "anthropic-beta": self.settings.claude_routine_beta,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                self.settings.claude_routine_url,
                headers=headers,
                json={"text": prompt},
            )
            response.raise_for_status()
        payload = response.json()
        session_id = payload["claude_code_session_id"]
        return ProviderRun(
            provider=self.name,
            run_id=session_id,
            session_id=session_id,
            status="prompted",
            url=payload.get("claude_code_session_url"),
            result="Claude Routine accepted the usage-check prompt; usage metadata requires its completion callback.",
            raw=payload,
        )


class GeminiAdapter(ProviderAdapter):
    name = ProviderName.GEMINI
    # Owned by antigravity.py so it is a member of APPROVED_STRINGS and can travel
    # inside the closed contract as well as label incoming results.
    NON_AUTHORITATIVE_NOTICE = NON_AUTHORITATIVE_NOTICE

    def _require_profile(self) -> None:
        failed = [
            name
            for name, ready in self.settings.gemini_supplemental_readiness().items()
            if not ready
        ]
        if failed:
            raise ProviderError(
                "Antigravity subordinate configuration is not ready: " + ", ".join(failed)
            )

    def _antigravity_input(
        self,
        assignment: Assignment,
        *,
        revision: bool = False,
        context: str | None = None,
        revision_prompt: str | None = None,
    ) -> str:
        """Build the phase-specific Antigravity envelope, or fail closed before HTTP.

        Ordinary work stays pure JSON of the closed-vocabulary contract, so every
        byte leaving this process is a repository constant, a closed-enum value, or
        a bounded integer. No assignment task, acceptance check, owner objective,
        dependency result, Slack text, or repository context is composed into it.

        An invited debate turn is the one prose-bearing envelope, and it is built
        only from the dedicated Director invitation plus the integrity-bound prior
        contributions that invitation declares. The generic primary-team handoff
        fields never cross this boundary in either phase.

        The rejected assignment's text never enters the error.
        `AntigravityContractError` carries generic labels only, and this is the one
        place they are turned into a `ProviderError` that the engine may write to
        `TaskRecord.error` and post to Slack.
        """
        self._require_profile()
        if assignment.discussion_phase == DiscussionPhase.WORK:
            errors = antigravity_assignment_errors(assignment)
            if errors:
                raise ProviderError(
                    "Antigravity assignment is outside its subordinate lane: "
                    + "; ".join(errors)
                )
        try:
            return antigravity_wire_input(
                assignment,
                revision=revision,
                context=context,
                revision_prompt=revision_prompt,
            )
        except AntigravityContractError as exc:
            raise ProviderError(
                "Antigravity assignment is outside its phase contract: "
                + "; ".join(exc.labels)
            ) from None

    @staticmethod
    def _output_text(payload: dict[str, Any]) -> str | None:
        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text:
            return output_text

        chunks: list[str] = []
        for step in payload.get("steps", []):
            if not isinstance(step, dict) or step.get("type") != "model_output":
                continue
            for content in step.get("content", []):
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    chunks.append(content["text"])
        return "".join(chunks) or None

    def _run(
        self,
        payload: dict[str, Any],
        *,
        session_id: str | None = None,
        fallback_run_id: str | None = None,
        post_submit: bool = False,
        subordinate_output: bool = False,
    ) -> ProviderRun:
        if not isinstance(payload, dict):
            error_type = PostSubmitAmbiguityError if post_submit else ProviderError
            raise error_type("Gemini Interaction response was not an object")
        run_id = payload.get("id") or fallback_run_id
        if not run_id:
            error_type = PostSubmitAmbiguityError if post_submit else ProviderError
            raise error_type("Gemini Interaction response did not include an ID")
        result = self._output_text(payload)
        if subordinate_output and result:
            result = f"{self.NON_AUTHORITATIVE_NOTICE}\n\n{result}"
        return ProviderRun(
            provider=self.name,
            run_id=run_id,
            session_id=payload.get("environment_id") or session_id,
            status=payload.get("status", "in_progress"),
            result=result,
            raw=payload,
        )

    @property
    def headers(self) -> dict[str, str]:
        return {
            "x-goog-api-key": self.settings.gemini_api_key,
            "Content-Type": "application/json",
            "Api-Revision": self.settings.gemini_api_revision,
        }

    @staticmethod
    def _error_code(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError, TypeError):
            payload = {}
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            for key in ("status", "code"):
                value = error.get(key)
                if isinstance(value, str) and value.strip():
                    normalized = value.strip().lower()
                    if normalized in {
                        "quota_exceeded",
                        "rate_limit_exceeded",
                        "resource_exhausted",
                    }:
                        return normalized
        return f"http_{response.status_code}"

    def _usage_retry_delay(self, response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        try:
            parsed = float(retry_after) if retry_after is not None else float(2**attempt)
        except (TypeError, ValueError):
            parsed = float(2**attempt)
        return min(
            max(parsed, 0.0),
            float(max(0, self.settings.gemini_usage_max_retry_delay_seconds)),
        )

    async def dispatch(self, assignment: Assignment, context: str) -> ProviderRun:
        # The debate builder extracts only its dedicated invitation and the
        # accepted declared dependencies that invitation names. The ordinary-work
        # builder ignores `context` entirely.
        prompt = self._antigravity_input(assignment, context=context)
        body = {
            "agent": self.settings.gemini_agent,
            "input": prompt,
            "environment": "remote",
            "background": True,
            "store": True,
            "agent_config": {
                "type": "antigravity",
                "max_total_tokens": self.settings.gemini_max_total_tokens,
            },
        }
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(f"{self.settings.gemini_api_base}/interactions", headers=self.headers, json=body)
            response.raise_for_status()
        payload = response.json()
        return self._run(payload, post_submit=True, subordinate_output=True)

    async def poll(self, run: ProviderRun) -> ProviderRun:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(f"{self.settings.gemini_api_base}/interactions/{run.run_id}", headers=self.headers)
            response.raise_for_status()
        payload = response.json()
        return self._run(
            payload, session_id=run.session_id, subordinate_output=True
        ).model_copy(update={"assignment_sequence": run.assignment_sequence})

    async def revise(
        self,
        run: ProviderRun,
        prompt: str,
        *,
        assignment: Assignment | None = None,
        context: str | None = None,
    ) -> ProviderRun:
        if assignment is None:
            raise ProviderError("Antigravity revision requires the bound assignment")
        # Ordinary work still ignores revision prose and is re-issued its own
        # original bounded request. A debate turn may carry the bounded Director
        # revision request, which the debate contract sanitizes and truncates.
        revision_input = self._antigravity_input(
            assignment,
            revision=True,
            context=context,
            revision_prompt=(
                prompt
                if assignment.discussion_phase != DiscussionPhase.WORK
                else None
            ),
        )
        if not run.session_id:
            # Falling back to a fresh "remote" sandbox while still chaining
            # previous_interaction_id would silently drop the prior files and
            # tool state, so the revision fails closed before any paid call.
            raise ProviderError(
                "Antigravity revision requires the prior sandbox environment ID"
            )
        body = {
            "agent": self.settings.gemini_agent,
            "previous_interaction_id": run.run_id,
            "environment": run.session_id,
            "input": revision_input,
            "background": True,
            "store": True,
            "agent_config": {
                "type": "antigravity",
                "max_total_tokens": self.settings.gemini_max_total_tokens,
            },
        }
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(f"{self.settings.gemini_api_base}/interactions", headers=self.headers, json=body)
            response.raise_for_status()
        payload = response.json()
        return self._run(
            payload,
            session_id=run.session_id,
            post_submit=True,
            subordinate_output=True,
        ).model_copy(
            update={"assignment_sequence": run.assignment_sequence}
        )

    async def usage_check(
        self, prompt: str, *, max_attempts: int | None = None
    ) -> ProviderRun:
        body = {
            "model": self.settings.gemini_usage_model,
            "input": prompt,
            "store": False,
        }
        configured_attempts = max(1, min(self.settings.gemini_usage_max_attempts, 5))
        if max_attempts is not None:
            configured_attempts = min(configured_attempts, max(1, max_attempts))
        attempt = 0
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                for attempt in range(1, configured_attempts + 1):
                    response = await client.post(
                        f"{self.settings.gemini_api_base}/interactions",
                        headers=self.headers,
                        json=body,
                    )
                    if response.status_code < 400:
                        break
                    if response.status_code != 429:
                        response.raise_for_status()
                    code = self._error_code(response)
                    if attempt >= configured_attempts:
                        raise ProviderError(
                            f"Gemini usage check failed with {code} after {attempt} bounded attempts",
                            code=code,
                            attempt_count=attempt,
                        )
                    await asyncio.sleep(self._usage_retry_delay(response, attempt - 1))
            payload = response.json()
            return self._run(
                payload,
                fallback_run_id="gemini-stateless-usage-check",
            ).model_copy(update={"attempt_count": attempt})
        except ProviderError:
            raise
        except Exception as exc:
            code = (
                "transport_error"
                if isinstance(exc, httpx.RequestError)
                else "invalid_response"
            )
            raise ProviderError(
                f"Gemini usage check failed with {code} after {max(1, attempt)} bounded attempts",
                code=code,
                attempt_count=max(1, attempt),
            ) from exc


class GrokAdapter(ProviderAdapter):
    """Primary Grok teammate with API and repository-capable CLI transports."""

    name = ProviderName.GROK

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self._catalog_verified = False

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.grok_api_key}",
            "Content-Type": "application/json",
        }

    def _require_profile(self, assignment: Assignment | None = None) -> None:
        failed = [
            name
            for name, ready in self.settings.grok_primary_readiness().items()
            if not ready
        ]
        if failed:
            raise ProviderError(
                "Grok primary API contact is not ready: "
                + ", ".join(failed)
            )
        if assignment is None:
            return
        if assignment.public_research_query is not None:
            raise ProviderError("Grok has no web-grounded research lane")
        trusted_estimate = max(
            assignment.estimated_cost_usd,
            self.settings.grok_request_cost_floor_usd,
        )
        if trusted_estimate > self.settings.grok_daily_budget_usd:
            raise ProviderError(
                "Grok assignment estimate exceeds the configured daily budget ceiling"
            )
        if self._requires_cli(assignment):
            repository = canonical_repository(assignment.repository or "")
            if (
                not self.settings.grok_cli_enabled
                or not self.settings.grok_cli_executable
                or repository not in self.settings.grok_cli_repository_map()
            ):
                raise ProviderError(
                    "Grok repository work requires an enabled CLI and an explicit "
                    "repository-to-worktree mapping"
                )

    @staticmethod
    def _requires_cli(assignment: Assignment) -> bool:
        return assignment.allow_draft_pr or assignment.kind == AssignmentKind.EXACT_SHA_REVIEW

    @staticmethod
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

    def _run(self, payload: dict[str, Any]) -> ProviderRun:
        if not isinstance(payload, dict) or not isinstance(payload.get("id"), str):
            raise ProviderError("Grok response did not include a valid response ID")
        status = payload.get("status")
        if not isinstance(status, str) or status != "completed":
            raise ProviderError(
                "Grok response did not include an explicit terminal-success status"
            )
        return ProviderRun(
            provider=self.name,
            run_id=payload["id"],
            status=status,
            result=self._output_text(payload),
            raw=payload,
        )

    async def _ensure_model_allowed(self, client: httpx.AsyncClient) -> None:
        if self._catalog_verified:
            return
        response = await client.get(
            f"{self.settings.grok_api_base}/models", headers=self.headers
        )
        response.raise_for_status()
        payload = response.json()
        models = {
            item.get("id")
            for item in payload.get("data", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        if self.settings.grok_model not in models:
            raise ProviderError(
                "Configured Grok model is unavailable to the authenticated xAI key"
            )
        self._catalog_verified = True

    def _input(
        self,
        assignment: Assignment,
        context: str,
        *,
        revision_prompt: str | None = None,
    ) -> str:
        if len(context) > self.settings.grok_max_context_chars:
            raise ProviderError(
                "Grok context exceeds the configured complete-packet limit; "
                "partial packet transmission is prohibited"
            )
        packet: dict[str, Any] = {
            "role": "primary_cursor_family_teammate",
            "task": assignment.task,
            "acceptance": assignment.acceptance,
            "context": context,
            "constraints": [
                "Complete the bounded assignment with concise evidence and an explicit result.",
                "Do not browse, call tools, write repositories, create branches or pull requests, merge, deploy, publish, purchase, or change access.",
                "Do not claim an external action that this API contact did not perform.",
            ],
        }
        if revision_prompt is not None:
            packet["revision_request"] = revision_prompt[:8_000]
        return json.dumps(packet, ensure_ascii=True, separators=(",", ":"))

    async def _git(self, root: Path, *args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(root),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()[:500]
            raise ProviderError(f"Grok CLI worktree validation failed: {detail}")
        return stdout.decode("utf-8", errors="replace").strip()

    async def _validated_cli_worktree(
        self, assignment: Assignment
    ) -> tuple[Path, dict[str, Any]]:
        repository = canonical_repository(assignment.repository or "")
        configured = self.settings.grok_cli_repository_map().get(repository)
        if configured is None:
            raise ProviderError("Grok CLI repository mapping is missing")
        root = Path(configured).expanduser().resolve()
        if not root.is_dir():
            raise ProviderError("Grok CLI mapped worktree does not exist")
        actual_root = Path(await self._git(root, "rev-parse", "--show-toplevel")).resolve()
        if actual_root != root:
            raise ProviderError("Grok CLI mapping must name the exact Git worktree root")
        if await self._git(root, "status", "--porcelain"):
            raise ProviderError("Grok CLI worktree must be clean before dispatch")
        branch = await self._git(root, "branch", "--show-current")
        evidence: dict[str, Any] = {
            "repository": repository,
            "worktree": str(root),
            "branch": branch,
        }
        if assignment.kind == AssignmentKind.EXACT_SHA_REVIEW:
            try:
                handoff = json.loads(assignment.task)
            except json.JSONDecodeError as exc:
                raise ProviderError("Grok exact-SHA review requires a structured handoff") from exc
            if not isinstance(handoff, dict):
                raise ProviderError("Grok exact-SHA review handoff must be an object")
            base_sha = handoff.get("base_sha")
            head_sha = handoff.get("head_sha")
            if not isinstance(base_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", base_sha):
                raise ProviderError("Grok review handoff has no valid base SHA")
            if not isinstance(head_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", head_sha):
                raise ProviderError("Grok review handoff has no valid head SHA")
            if canonical_repository(str(handoff.get("repository", ""))) != repository:
                raise ProviderError("Grok review handoff repository does not match the worktree")
            await self._git(root, "cat-file", "-e", f"{base_sha}^{{commit}}")
            await self._git(root, "cat-file", "-e", f"{head_sha}^{{commit}}")
            await self._git(root, "merge-base", "--is-ancestor", base_sha, head_sha)
            evidence.update(
                {
                    "mode": "exact_sha_review",
                    "base_sha": base_sha,
                    "head_sha": head_sha,
                    "read_only": True,
                }
            )
        elif assignment.allow_draft_pr:
            if not branch.startswith("grok/"):
                raise ProviderError(
                    "Grok draft-PR work requires a clean isolated grok/* worktree"
                )
            evidence.update({"mode": "draft_pr", "read_only": False})
        else:
            raise ProviderError("Grok CLI transport was selected for an unsupported assignment")
        return root, evidence

    @staticmethod
    def _cli_result(payload: Any) -> str | None:
        if isinstance(payload, dict) and payload.get("artifact_type"):
            return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        if not isinstance(payload, dict):
            return None
        for key in ("result", "output_text", "text", "message"):
            value = payload.get(key)
            if isinstance(value, dict):
                return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    async def _dispatch_cli(
        self,
        assignment: Assignment,
        context: str,
        *,
        revision_prompt: str | None = None,
    ) -> ProviderRun:
        root, evidence = await self._validated_cli_worktree(assignment)
        run_id = f"grok-cli-{secrets.token_hex(12)}"
        instructions = [
            "Operate only in the explicit worktree and assignment scope.",
            "Do not expose credentials, widen access, deploy, publish, purchase, or merge.",
            "Return the exact JSON artifact required by the assignment.",
        ]
        if evidence["mode"] == "exact_sha_review":
            instructions.extend(
                [
                    f"Independently inspect git diff {evidence['base_sha']}..{evidence['head_sha']} and the named tests.",
                    "Do not modify files, refs, branches, remotes, reviews, or pull requests.",
                    f"Set review_run_id to {run_id} in the returned review artifact.",
                ]
            )
        else:
            instructions.append(
                "Create only the authorized isolated branch changes and one draft pull request; never merge it."
            )
        prompt = json.dumps(
            {
                "assignment": assignment.model_dump(mode="json"),
                "context": context,
                "revision_request": revision_prompt,
                "fixed_instructions": instructions,
            },
            ensure_ascii=True,
            separators=(",", ":"),
        )
        prompt_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".json", delete=False
            ) as handle:
                handle.write(prompt)
                prompt_path = Path(handle.name)
            permission_mode = "plan" if evidence["read_only"] else "auto"
            process = await asyncio.create_subprocess_exec(
                self.settings.grok_cli_executable,
                "--prompt-file",
                str(prompt_path),
                "--output-format",
                "json",
                "--cwd",
                str(root),
                "--disable-web-search",
                "--no-subagents",
                "--max-turns",
                str(self.settings.grok_cli_max_turns),
                "--permission-mode",
                permission_mode,
                "--verbatim",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=self.settings.grok_cli_timeout_seconds
                )
            except TimeoutError:
                process.kill()
                await process.wait()
                raise ProviderError("Grok CLI assignment timed out")
            if process.returncode != 0:
                detail = stderr.decode("utf-8", errors="replace").strip()[:1_000]
                raise ProviderError(f"Grok CLI assignment failed: {detail}")
            try:
                payload = json.loads(stdout.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ProviderError("Grok CLI returned invalid JSON") from exc
            result = self._cli_result(payload)
            if not result:
                raise ProviderError("Grok CLI returned no reviewable result")
            return ProviderRun(
                provider=self.name,
                run_id=run_id,
                status="completed",
                result=result,
                raw={"transport": "grok_cli", **evidence},
            )
        finally:
            if prompt_path is not None:
                prompt_path.unlink(missing_ok=True)

    async def _submit(self, input_text: str) -> ProviderRun:
        body = {
            "model": self.settings.grok_model,
            "input": input_text,
            "max_output_tokens": self.settings.grok_max_output_tokens,
            "tools": [],
            "store": False,
        }
        async with httpx.AsyncClient(timeout=self.settings.grok_timeout_seconds) as client:
            await self._ensure_model_allowed(client)
            response = await client.post(
                f"{self.settings.grok_api_base}/responses",
                headers=self.headers,
                json=body,
            )
            response.raise_for_status()
        return self._run(response.json())

    async def dispatch(self, assignment: Assignment, context: str) -> ProviderRun:
        self._require_profile(assignment)
        if self._requires_cli(assignment):
            return await self._dispatch_cli(assignment, context)
        return await self._submit(self._input(assignment, context))

    async def poll(self, run: ProviderRun) -> ProviderRun:
        raise ProviderError("Grok Responses API calls are synchronous and cannot be polled")

    async def revise(
        self,
        run: ProviderRun,
        prompt: str,
        *,
        assignment: Assignment | None = None,
        context: str | None = None,
    ) -> ProviderRun:
        del run
        if assignment is None or context is None:
            raise ProviderError("Grok revision requires the bound assignment and context")
        self._require_profile(assignment)
        if self._requires_cli(assignment):
            return await self._dispatch_cli(
                assignment, context, revision_prompt=prompt
            )
        return await self._submit(
            self._input(assignment, context, revision_prompt=prompt)
        )

    async def usage_check(self, prompt: str) -> ProviderRun:
        self._require_profile()
        return await self._submit(prompt)


class PerplexityAdapter(ProviderAdapter):
    name = ProviderName.PERPLEXITY

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.settings.perplexity_api_key}", "Content-Type": "application/json"}

    def _require_research_only(self, assignment: Assignment | None = None) -> None:
        failed = [
            name
            for name, ready in self.settings.perplexity_research_readiness().items()
            if not ready
        ]
        if failed:
            raise ProviderError(
                "Perplexity research-only configuration is not ready: " + ", ".join(failed)
            )
        if assignment is not None:
            if assignment.allow_draft_pr:
                raise ProviderError("Perplexity research assignments cannot create draft pull requests")
            if assignment.estimated_cost_usd > self.settings.perplexity_daily_budget_usd:
                raise ProviderError(
                    "Perplexity assignment estimate exceeds the configured research budget ceiling"
                )

    @staticmethod
    def _search_run(payload: dict[str, Any], query: str) -> ProviderRun:
        retrieved_at = datetime.now(timezone.utc).isoformat()
        evidence = {
            "query": query,
            "retrieved_at": retrieved_at,
            "results": [
                {
                    "result_url": item.get("url"),
                    "page_title": item.get("title"),
                    "publication_date": item.get("date"),
                    "last_updated": item.get("last_updated"),
                    "supporting_excerpt": item.get("snippet"),
                    "source_type": "web_result",
                    "confidence": "not_provided_by_search_api",
                    "contradictions_and_unavailable_evidence": "not_evaluated_in_raw_search",
                }
                for item in payload.get("results", [])
                if isinstance(item, dict)
            ],
        }
        return ProviderRun(
            provider=ProviderName.PERPLEXITY,
            run_id=payload["id"],
            status="completed",
            result=json.dumps(evidence, sort_keys=True),
            raw=payload,
        )

    @staticmethod
    def _run(payload: dict[str, Any]) -> ProviderRun:
        result = payload.get("output_text")
        if not result:
            chunks = []
            for item in payload.get("output", []):
                for content in item.get("content", []) if isinstance(item, dict) else []:
                    if isinstance(content, dict) and isinstance(content.get("text"), str):
                        chunks.append(content["text"])
            result = "".join(chunks) or None
        return ProviderRun(
            provider=ProviderName.PERPLEXITY,
            run_id=payload["id"],
            status=payload.get("status", "completed"),
            result=result,
            raw=payload,
        )

    async def dispatch(self, assignment: Assignment, context: str) -> ProviderRun:
        del context  # Search receives only the bounded query, never repository/task context.
        self._require_research_only(assignment)
        if not assignment.public_research_query:
            raise ProviderError("Perplexity requires a bounded public research query")
        body = {
            "query": assignment.public_research_query,
            "max_results": self.settings.perplexity_max_results,
        }
        async with httpx.AsyncClient(timeout=self.settings.perplexity_timeout_seconds) as client:
            response = await client.post(f"{self.settings.perplexity_api_base}/search", headers=self.headers, json=body)
            response.raise_for_status()
        payload = response.json()
        return self._search_run(payload, assignment.public_research_query)

    async def poll(self, run: ProviderRun) -> ProviderRun:
        raise ProviderError("Perplexity API responses are synchronous and cannot be polled")

    async def revise(
        self,
        run: ProviderRun,
        prompt: str,
        *,
        assignment: Assignment | None = None,
        context: str | None = None,
    ) -> ProviderRun:
        del run, prompt, context  # Director feedback is private and never becomes a query.
        if assignment is None:
            raise ProviderError("Perplexity requires a bounded public research query")
        self._require_research_only(assignment)
        raise ProviderError(
            "Perplexity revision requires a new owner-approved bounded public research query"
        )

    async def usage_check(self, prompt: str) -> ProviderRun:
        self._require_research_only()
        body = {
            "preset": self.settings.perplexity_preset,
            "input": prompt,
            "max_output_tokens": self.settings.usage_check_max_output_tokens,
            "tools": [],
        }
        async with httpx.AsyncClient(timeout=self.settings.perplexity_timeout_seconds) as client:
            response = await client.post(
                f"{self.settings.perplexity_api_base}/v1/agent",
                headers=self.headers,
                json=body,
            )
            response.raise_for_status()
        payload = response.json()
        return self._run(payload)


def provider_registry(settings: Settings) -> dict[ProviderName, ProviderAdapter]:
    return {
        ProviderName.CURSOR: CursorAdapter(settings),
        ProviderName.CLAUDE: ClaudeAdapter(settings),
        ProviderName.GEMINI: GeminiAdapter(settings),
        ProviderName.GROK: GrokAdapter(settings),
        ProviderName.PERPLEXITY: PerplexityAdapter(settings),
    }
# [[OpenAI Codex | GPT-5.6 | 2026-08-24 | 23:46 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 01:59 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 22:11 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 00:25 UTC | END]]
