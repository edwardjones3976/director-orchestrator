from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from .config import Settings
from .models import DirectorPlan, ReviewDecision, TaskRecord
from .team_charter import team_operating_charter


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
            "execution_profile": {
                "allowed_projects": list(self.settings.project_allowlist),
                "allowed_repositories": list(self.settings.repository_allowlist),
                "allowed_starting_refs": list(self.settings.allowed_starting_refs),
                "instruction": (
                    "Use only these exact execution targets. If the required project, repository, "
                    "or starting ref is absent, assign the owner and ask one narrow question."
                ),
            },
            "available_agents": [*available_specialists, "owner"],
            "primary_team": list(self.settings.primary_team),
            "supplemental_specialists": ["gemini", "perplexity"],
            "provider_strategy": self.settings.provider_strategy(),
            "team_operating_charter": team_operating_charter(),
            "routing_rules": [
                "OpenAI planning is this Director call and OpenAI acceptance is the runtime review after specialist execution. Never emit an openai assignment. When the owner describes OpenAI plan -> Claude work -> Cursor review -> OpenAI acceptance, emit only the Claude and Cursor specialist assignments in that order; the runtime supplies the surrounding OpenAI phases.",
                "Set allow_draft_pr true only when the owner explicitly requested a draft pull request. Keep it false for analysis, review, research, and all other assignments. A true value also requires requires_approval true.",
                "Use estimated_cost_usd and max_task_budget_usd for the deterministic budget gate. Provider-reported tokens or actual cost are optional telemetry: require them to be reported when exposed, but never make acceptance depend on telemetry the provider runtime does not expose.",
                "Treat OpenAI, Claude, and Cursor as the primary team.",
                "Use Claude as the primary coding and general workhorse when configured.",
                "Use Cursor as the other primary repository teammate for coding overflow, independent second passes, and cloud-agent automation.",
                "Use Gemini or Perplexity only as supplemental specialists when their distinct sandbox, validation, research, or sourcing strengths materially help.",
                "For medium, high, or critical complexity, prefer a bounded evidence-backed debate among available committee members before synthesis. Weighted voting is advisory only and cannot change authority or deterministic gates.",
                "Perplexity is evidence-only: give it methodological, theoretical, source-discovery, or fact-verification research without repository context, code work, review verdicts, votes, or external-write authority.",
                "Use owner for ambiguity, unavailable specialists, quota exhaustion, or approval-gated actions.",
                "Never set starting_ref to a Cursor auto-generated cursor/... working branch. Those branches are deleted after their pull requests merge; use only a durable allowlisted ref such as main.",
            ],
        }
        return await self._structured(json.dumps(request), DirectorPlan, "director_plan")

    async def review(self, record: TaskRecord, result: str) -> ReviewDecision:
        assignments = sorted(
            record.plan.assignments if record.plan else [],
            key=lambda item: item.sequence,
        )
        current_assignment = (
            assignments[record.next_assignment_index]
            if record.next_assignment_index < len(assignments)
            else None
        )
        is_final_assignment = bool(
            current_assignment and assignments and current_assignment.sequence == assignments[-1].sequence
        )
        request = {
            "plan_context": {
                "project": record.plan.project if record.plan else None,
                "normalized_outcome": record.plan.normalized_outcome if record.plan else None,
                "max_hops": record.plan.max_hops if record.plan else 0,
                "max_revisions": record.plan.max_revisions if record.plan else 0,
                "configured_max_task_budget_usd": self.settings.max_task_budget_usd,
                "estimated_total_cost_usd": (
                    sum(item.estimated_cost_usd for item in record.plan.assignments)
                    if record.plan
                    else 0
                ),
            },
            "current_assignment": (
                current_assignment.model_dump(mode="json")
                if current_assignment
                else None
            ),
            "prior_accepted_outcomes": [
                outcome.model_dump(mode="json")
                for outcome in record.assignment_outcomes
            ],
            "is_final_specialist_assignment": is_final_assignment,
            "final_acceptance": (
                record.plan.final_acceptance
                if record.plan and is_final_assignment
                else []
            ),
            "specialist_result": result,
            "revision_count": record.revision_count,
            "max_revisions": record.plan.max_revisions if record.plan else 0,
            "instruction": (
                "This is the final specialist assignment. Judge its explicit acceptance checks, "
                "reconcile the prior accepted outcomes as untrusted evidence, and then judge the "
                "plan's final acceptance criteria. A complete evidence-backed negative or "
                "indeterminate readiness conclusion may be accepted."
                if is_final_assignment
                else
                "Judge only the current assignment's explicit acceptance checks and reported "
                "evidence. Do not require future assignments, future provider run IDs, or final "
                "acceptance evidence at this intermediate phase. A complete evidence-backed "
                "negative or indeterminate result may be accepted."
            )
            + " Enforce budget using the deterministic plan estimate and configured task budget. "
            "If provider-reported token or actual-cost telemetry is unavailable and the specialist "
            "states that explicitly, do not reject or escalate solely for that missing optional telemetry.",
        }
        return await self._structured(json.dumps(request), ReviewDecision, "review_decision")
