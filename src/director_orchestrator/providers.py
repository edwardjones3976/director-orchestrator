from __future__ import annotations

import json
from typing import Any

import httpx

from .config import Settings
from .models import Assignment, ProviderName, ProviderRun


class ProviderError(RuntimeError):
    pass


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

    async def dispatch(self, assignment: Assignment, context: str) -> ProviderRun:
        body: dict[str, Any] = {
            "name": f"Director task {assignment.sequence}",
            "prompt": {"text": f"{assignment.task}\n\nAcceptance checks:\n- " + "\n- ".join(assignment.acceptance) + f"\n\nContext:\n{context}"},
            "workOnCurrentBranch": False,
            "autoCreatePR": True,
            "skipReviewerRequest": False,
        }
        if assignment.repository:
            body["repos"] = [{"url": assignment.repository, "startingRef": assignment.starting_ref}]
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(f"{self.settings.cursor_api_base}/v1/agents", headers=self.headers, json=body)
            response.raise_for_status()
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
            response.raise_for_status()
        payload = response.json()["run"]
        return ProviderRun(provider=self.name, run_id=payload["id"], session_id=run.session_id, status=payload["status"], url=run.url, raw=payload)

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

    @property
    def headers(self) -> dict[str, str]:
        return {
            "x-goog-api-key": self.settings.gemini_api_key,
            "Content-Type": "application/json",
            "Api-Revision": self.settings.gemini_api_revision,
        }

    async def dispatch(self, assignment: Assignment, context: str) -> ProviderRun:
        prompt = f"{assignment.task}\n\nAcceptance checks:\n- " + "\n- ".join(assignment.acceptance) + f"\n\nContext:\n{context}"
        body = {
            "model": self.settings.gemini_agent,
            "input": prompt,
            "environment": "remote",
            "background": True,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(f"{self.settings.gemini_api_base}/interactions", headers=self.headers, json=body)
            response.raise_for_status()
        payload = response.json()
        return ProviderRun(
            provider=self.name,
            run_id=payload["id"],
            session_id=payload.get("environment_id"),
            status=payload.get("status", "in_progress"),
            raw=payload,
        )

    async def poll(self, run: ProviderRun) -> ProviderRun:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(f"{self.settings.gemini_api_base}/interactions/{run.run_id}", headers=self.headers)
            response.raise_for_status()
        payload = response.json()
        return run.model_copy(update={"status": payload.get("status", run.status), "result": payload.get("output_text"), "raw": payload})

    async def revise(self, run: ProviderRun, prompt: str) -> ProviderRun:
        body = {
            "model": self.settings.gemini_agent,
            "previous_interaction_id": run.run_id,
            "environment": "remote",
            "input": prompt,
            "background": True,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(f"{self.settings.gemini_api_base}/interactions", headers=self.headers, json=body)
            response.raise_for_status()
        payload = response.json()
        return ProviderRun(provider=self.name, run_id=payload["id"], session_id=payload.get("environment_id", run.session_id), status=payload.get("status", "in_progress"), raw=payload)

    async def usage_check(self, prompt: str) -> ProviderRun:
        body = {
            "model": self.settings.gemini_agent,
            "input": prompt,
            "environment": "remote",
            "background": True,
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
        return ProviderRun(
            provider=self.name,
            run_id=payload["id"],
            session_id=payload.get("environment_id"),
            status=payload.get("status", "in_progress"),
            raw=payload,
        )


class PerplexityAdapter(ProviderAdapter):
    name = ProviderName.PERPLEXITY

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.settings.perplexity_api_key}", "Content-Type": "application/json"}

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
        body = {
            "preset": self.settings.perplexity_preset,
            "input": f"{assignment.task}\n\nAcceptance checks:\n- " + "\n- ".join(assignment.acceptance) + f"\n\nContext:\n{context}",
            "tools": [{"type": "web_search"}],
        }
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(f"{self.settings.perplexity_api_base}/v1/agent", headers=self.headers, json=body)
            response.raise_for_status()
        payload = response.json()
        return self._run(payload)

    async def poll(self, run: ProviderRun) -> ProviderRun:
        raise ProviderError("Perplexity Agent API responses are synchronous and cannot be polled")

    async def revise(self, run: ProviderRun, prompt: str) -> ProviderRun:
        body = {"preset": self.settings.perplexity_preset, "previous_response_id": run.run_id, "input": prompt}
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(f"{self.settings.perplexity_api_base}/v1/agent", headers=self.headers, json=body)
            response.raise_for_status()
        payload = response.json()
        return self._run(payload)

    async def usage_check(self, prompt: str) -> ProviderRun:
        body = {
            "preset": self.settings.perplexity_preset,
            "input": prompt,
            "max_output_tokens": self.settings.usage_check_max_output_tokens,
            "tools": [],
        }
        async with httpx.AsyncClient(timeout=60) as client:
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
