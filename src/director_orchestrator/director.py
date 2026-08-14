from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from .config import Settings
from .models import DirectorPlan, ReviewDecision, TaskRecord


def _openai_strict_subset(value: Any) -> Any:
    if isinstance(value, list):
        return [_openai_strict_subset(item) for item in value]
    if not isinstance(value, dict):
        return value
    cleaned = {
        key: _openai_strict_subset(item)
        for key, item in value.items()
        if key != "default"
    }
    properties = cleaned.get("properties")
    if isinstance(properties, dict):
        cleaned["additionalProperties"] = False
        cleaned["required"] = list(properties)
    return cleaned


def _strict_schema(model: type[DirectorPlan] | type[ReviewDecision], name: str) -> dict[str, Any]:
    schema = _openai_strict_subset(model.model_json_schema())
    return {"type": "json_schema", "name": name, "strict": True, "schema": schema}


def _output_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    chunks: list[str] = []
    for item in payload.get("output", []):
        for content in item.get("content", []) if isinstance(item, dict) else []:
            if content.get("type") in {"output_text", "text"} and isinstance(content.get("text"), str):
                chunks.append(content["text"])
    if not chunks:
        raise RuntimeError("OpenAI response did not contain output text")
    return "".join(chunks)


class OpenAIDirector:
    def __init__(self, settings: Settings):
        self.settings = settings
        prompt_path = Path(__file__).resolve().parents[2] / "prompts" / "director-system.md"
        self.instructions = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else "Create a bounded agent plan."

    async def _structured(self, input_text: str, model: type[DirectorPlan] | type[ReviewDecision], name: str):
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {
            "model": self.settings.openai_model,
            "instructions": self.instructions,
            "input": input_text,
            "text": {"format": _strict_schema(model, name)},
            "store": False,
        }
        if self.settings.openai_reasoning_effort:
            body["reasoning"] = {"effort": self.settings.openai_reasoning_effort}
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post("https://api.openai.com/v1/responses", headers=headers, json=body)
            response.raise_for_status()
        return model.model_validate(json.loads(_output_text(response.json())))

    async def plan(self, record: TaskRecord) -> DirectorPlan:
        readiness = self.settings.readiness()
        available_specialists = [
            name
            for name in ("claude", "cursor", "gemini", "perplexity")
            if readiness.get(name, False)
        ]
        request = {
            "owner_objective": record.intake.text,
            "hard_limits": {
                "max_hops": self.settings.max_agent_hops,
                "max_revisions": self.settings.max_revisions,
                "max_task_budget_usd": self.settings.max_task_budget_usd,
            },
            "available_agents": [*available_specialists, "owner"],
            "primary_team": list(self.settings.primary_team),
            "supplemental_specialists": ["gemini", "perplexity"],
            "provider_strategy": self.settings.provider_strategy(),
            "routing_rules": [
                "Treat OpenAI, Claude, and Cursor as the primary team.",
                "Use Claude as the primary coding and general workhorse when configured.",
                "Use Cursor as the other primary repository teammate for coding overflow, independent second passes, and cloud-agent automation.",
                "Use Gemini or Perplexity only as supplemental specialists when their distinct sandbox, validation, research, or sourcing strengths materially help.",
                "Use owner for ambiguity, unavailable specialists, quota exhaustion, or approval-gated actions.",
            ],
        }
        return await self._structured(json.dumps(request), DirectorPlan, "director_plan")

    async def review(self, record: TaskRecord, result: str) -> ReviewDecision:
        request = {
            "plan": record.plan.model_dump(mode="json") if record.plan else None,
            "specialist_result": result,
            "revision_count": record.revision_count,
            "max_revisions": record.plan.max_revisions if record.plan else 0,
            "instruction": "Judge only against explicit acceptance checks and reported evidence.",
        }
        return await self._structured(json.dumps(request), ReviewDecision, "review_decision")
