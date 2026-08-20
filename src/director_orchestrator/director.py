from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from .config import Settings
from .models import DebateSynthesis, DirectorPlan, DiscussionPhase, ReviewDecision, TaskRecord
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


def _strict_schema(
    model: type[DirectorPlan] | type[ReviewDecision] | type[DebateSynthesis],
    name: str,
) -> dict[str, Any]:
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

    async def _structured(
        self,
        input_text: str,
        model: type[DirectorPlan] | type[ReviewDecision] | type[DebateSynthesis],
        name: str,
    ):
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
                "Treat Cursor as a full-spectrum primary repository partner with a 90 percent capability-parity target relative to Claude and OpenAI Codex. Route low through critical work to Cursor by fit, independence, capacity, and evidence needs; do not reserve it only for overflow or second passes.",
                "Cursor may handle research, evidence verification, planning support, deep reasoning, elevated architecture and reliability work, complex implementation, QA, innovation, and leadership. It still cannot replace OpenAI acceptance, mandatory Codex Security, or an independent reviewer for its own work.",
                "Use Gemini Antigravity only as a subordinate supplemental specialist for explicit boilerplate, scaffolding, low-level mechanical implementation, or alternate ideation. Its output is non-authoritative evidence: it cannot create a draft pull request, perform exact-SHA review, final acceptance, or deterministic merge, or define contract or methodology truth. Never route generic prototype, browser, visual, core methodology, temporal eligibility, evaluation, provenance, or acceptance work to it by keyword alone.",
                "Every Antigravity work assignment must be followed by a Claude or Cursor work assignment that directly depends on it and independently verifies and integrates or rejects the artifact. The runtime binds that integration wording deterministically.",
                "Set kind on every assignment. Gemini work assignments permit only boilerplate, scaffolding, low_level_implementation, or abstract_ideation; Perplexity uses research; exact-SHA reviewers use exact_sha_review; use implementation or general otherwise.",
                "Use Perplexity only as a supplemental specialist when its distinct public-source research and sourcing strengths materially help.",
                "For medium, high, or critical complexity, prefer a bounded evidence-backed debate among available committee members before synthesis. Weighted voting is advisory only and cannot change authority or deterministic gates.",
                "A debate is a first-class runtime feature. For medium, high, or critical work, emit a debate whenever at least two of Claude, Cursor, and Gemini are configured and the hop/budget limits permit it. Create independent position assignments using discussion_phase=position, then exactly one rebuttal assignment per participant using discussion_phase=rebuttal. Populate debate.position_assignments and debate.rebuttal_assignments exactly; each rebuttal must depend on every position. Perplexity may supply a prior public evidence assignment but can never be a debate participant or voter.",
                "Count a producer's advisory vote at full configured weight while keeping that producer ineligible to review or accept its own work. Abstentions and unavailable members cast zero weight and never change the fixed configured denominator.",
                "Resolve an ordinary advisory tie by documented OpenAI evidence judgment. Ask the owner one focused question when tied options materially differ in cost, authority, safety, privacy, repository scope, runtime behavior, or project direction.",
                "After three bounded recorded idempotent-safe nonresponse attempts, mark the provider unavailable for this assignment cycle, preserve partial output as untrusted evidence, continue independent safe work, and report scope, confidence, timing, and coverage changes. Never repeat an ambiguous paid dispatch.",
                "Pre-budget a different-provider review assignment for every requested draft PR or other reviewable implementation. The runtime binds its structured handoff to the logical producer, material authors, exact base/head SHA or artifact digest, changed files, tests, limitations, acceptance checks, and security context. A new commit invalidates prior review, Codex Security, CI, and merge evidence.",
                "Perplexity is evidence-only: give it methodological, theoretical, source-discovery, or fact-verification research without repository context, code work, review verdicts, votes, or external-write authority. Set requires_approval=true so the owner sees and approves the exact outbound query. Set public_research_query to a bounded public query containing no repository path or URL, commit SHA, source excerpt, private identifier, or secret-like value; set it null for every other provider.",
                "For owner-gated provider-UI coordination, use an already authenticated Chrome session first and Microsoft Edge only when Chrome is unavailable. Limit this to Claude, Cursor, or Gemini inside the stored assignment; never bypass authentication, consent, CAPTCHA, privacy, device, workspace-trust, permission, or security prompts, expose credentials or raw private source, change billing or access, post externally, merge, deploy, purchase, or treat browser output as authenticated provider or gate evidence. Perplexity remains API-only.",
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
                    + (
                        record.plan.debate.estimated_synthesis_cost_usd
                        if record.plan.debate
                        else 0
                    )
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
            "states that explicitly, do not reject or escalate solely for that missing optional telemetry."
            " Keep summary and evidence concise and safe to relay in the assignment's private Slack "
            "thread: never include credentials, secret-like values, raw private source, or unrelated project material."
            + (
                " This is a structured debate contribution. If accepting it, populate "
                "debate_contribution with a concise position, bounded evidence and contradictions, "
                "confidence, and exactly one vote_option from the debate plan or abstain=true. "
                "Do not copy secrets, raw private source, or unrelated project context into it."
                if current_assignment
                and current_assignment.discussion_phase != DiscussionPhase.WORK
                else " Set debate_contribution to null for ordinary work assignments."
            ),
        }
        return await self._structured(json.dumps(request), ReviewDecision, "review_decision")

    async def synthesize_debate(self, record: TaskRecord) -> DebateSynthesis:
        if record.plan is None or record.plan.debate is None:
            raise ValueError("debate synthesis requires a debate plan")
        request = {
            "task_id": record.task_id,
            "project": record.plan.project,
            "question": record.plan.debate.question,
            "vote_options": record.plan.debate.vote_options,
            "material_risk": record.plan.debate.material_risk,
            "turns": [
                turn.model_dump(mode="json")
                for turn in record.collaboration_turns
                if turn.phase in {"position", "rebuttal"}
            ],
            "unavailable_providers": [item.value for item in record.unavailable_providers],
            "availability_impacts": record.availability_impacts,
            "instruction": (
                "Synthesize the bounded debate without inventing missing evidence. Preserve meaningful "
                "dissent and select exactly one configured vote option or abstain. Your vote is advisory. "
                "Do not authorize merge, deployment, publication, spending, access, or any other action. "
                "State one bounded next action. A material tie is handled deterministically after this call."
            ),
        }
        return await self._structured(
            json.dumps(request), DebateSynthesis, "debate_synthesis"
        )
