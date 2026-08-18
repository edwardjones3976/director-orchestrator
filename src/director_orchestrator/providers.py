from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

import httpx

from .config import Settings
from .models import Assignment, ProviderName, ProviderRun
from .policy import antigravity_assignment_errors

_CURSOR_WORKING_REF_PREFIX = "cursor/"
_MISSING_STARTING_REF_STATUSES = {400, 404, 409, 422}


class ProviderError(RuntimeError):
    pass


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
    _NON_AUTHORITATIVE_NOTICE = (
        "NON-AUTHORITATIVE ANTIGRAVITY ARTIFACT: This output is subordinate input. "
        "Claude or Cursor must independently verify and integrate or reject it before use."
    )

    def _require_profile(self) -> None:
        failed = [
            name
            for name, ready in self.settings.antigravity_subordinate_readiness().items()
            if not ready
        ]
        if failed:
            raise ProviderError(
                "Antigravity subordinate configuration is not ready: " + ", ".join(failed)
            )

    def _require_subordinate(self, assignment: Assignment) -> None:
        self._require_profile()
        errors = antigravity_assignment_errors(assignment)
        if errors:
            raise ProviderError(
                "Antigravity assignment is outside its subordinate lane: "
                + "; ".join(errors)
            )

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
        subordinate_output: bool = False,
    ) -> ProviderRun:
        run_id = payload.get("id") or fallback_run_id
        if not run_id:
            raise ProviderError("Gemini Interaction response did not include an ID")
        result = self._output_text(payload)
        if subordinate_output and result:
            result = f"{self._NON_AUTHORITATIVE_NOTICE}\n\n{result}"
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

    async def dispatch(self, assignment: Assignment, context: str) -> ProviderRun:
        self._require_subordinate(assignment)
        prompt = (
            f"{self._NON_AUTHORITATIVE_NOTICE}\n\n"
            "Stay within bounded boilerplate, scaffolding, low-level mechanical implementation, "
            "or alternate ideation. Do not issue review, acceptance, merge, contract, or "
            "methodology verdicts. Clearly separate tested artifacts from abstract ideas.\n\n"
            f"Task:\n{assignment.task}\n\nAcceptance checks:\n- "
            + "\n- ".join(assignment.acceptance)
            + f"\n\nContext:\n{context}"
        )
        body = {
            "agent": self.settings.gemini_agent,
            "input": prompt,
            "environment": "remote",
            "background": True,
            "store": True,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(f"{self.settings.gemini_api_base}/interactions", headers=self.headers, json=body)
            response.raise_for_status()
        payload = response.json()
        return self._run(payload, subordinate_output=True)

    async def poll(self, run: ProviderRun) -> ProviderRun:
        self._require_profile()
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(f"{self.settings.gemini_api_base}/interactions/{run.run_id}", headers=self.headers)
            response.raise_for_status()
        payload = response.json()
        return self._run(
            payload,
            session_id=run.session_id,
            subordinate_output=True,
        ).model_copy(
            update={"assignment_sequence": run.assignment_sequence}
        )

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
        self._require_subordinate(assignment)
        body = {
            "agent": self.settings.gemini_agent,
            "previous_interaction_id": run.run_id,
            "environment": run.session_id or "remote",
            "input": f"{self._NON_AUTHORITATIVE_NOTICE}\n\n{prompt}",
            "background": True,
            "store": True,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(f"{self.settings.gemini_api_base}/interactions", headers=self.headers, json=body)
            response.raise_for_status()
        payload = response.json()
        return self._run(
            payload,
            session_id=run.session_id,
            subordinate_output=True,
        ).model_copy(
            update={"assignment_sequence": run.assignment_sequence}
        )

    async def usage_check(self, prompt: str) -> ProviderRun:
        self._require_profile()
        body = {
            "model": self.settings.gemini_usage_model,
            "input": prompt,
            "store": False,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.settings.gemini_api_base}/interactions",
                headers=self.headers,
                json=body,
            )
            response.raise_for_status()
        payload = response.json()
        return self._run(payload, fallback_run_id="gemini-stateless-usage-check")


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
        body = {
            "query": assignment.task,
            "max_results": self.settings.perplexity_max_results,
        }
        async with httpx.AsyncClient(timeout=self.settings.perplexity_timeout_seconds) as client:
            response = await client.post(f"{self.settings.perplexity_api_base}/search", headers=self.headers, json=body)
            response.raise_for_status()
        payload = response.json()
        return self._search_run(payload, assignment.task)

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
        del run, context
        self._require_research_only(assignment)
        body = {"query": prompt, "max_results": self.settings.perplexity_max_results}
        async with httpx.AsyncClient(timeout=self.settings.perplexity_timeout_seconds) as client:
            response = await client.post(f"{self.settings.perplexity_api_base}/search", headers=self.headers, json=body)
            response.raise_for_status()
        payload = response.json()
        return self._search_run(payload, prompt)

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
        ProviderName.PERPLEXITY: PerplexityAdapter(settings),
    }
