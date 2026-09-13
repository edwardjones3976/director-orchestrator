# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:56 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:30 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:38 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:21 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 19:11 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 10:40 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:00 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:23 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:18 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 22:11 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 14:01 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 13:34 UTC | BEGIN]]
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from .allocation_profiles import allocation_targets
from .config import Settings
from .interactions import ProviderTransport
from .models import (
    DebateCounterBias,
    DebateSynthesis,
    DebateTieJudgment,
    DirectorPlan,
    DiscussionPhase,
    ProviderName,
    ReviewDecision,
    TaskRecord,
)
from .policy import estimated_plan_cost_usd
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
    model: (
        type[DirectorPlan]
        | type[ReviewDecision]
        | type[DebateCounterBias]
        | type[DebateSynthesis]
        | type[DebateTieJudgment]
    ),
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
    provider = ProviderName.OPENAI
    transport = ProviderTransport.OPENAI_RESPONSES_API

    @property
    def model_name(self) -> str:
        return self.settings.openai_model

    def __init__(self, settings: Settings):
        self.settings = settings
        prompt_path = Path(__file__).resolve().parents[2] / "prompts" / "director-system.md"
        self.instructions = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else "Create a bounded agent plan."
        self.instructions = (
            f"Execution identity: this invocation is {self.provider.value}, model {self.model_name}. "
            "Claude is the acting project Director; that leadership role does not change this "
            "invocation's actual provider identity or authorize impersonation. Apply the shared "
            "policy as the executing runtime planner/reviewer. Preserve historical field names.\n\n"
            + self.instructions
        )

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
        profile = record.intake.allocation_profile
        profile_weights = ", ".join(f"{lane} {weight}%" for lane, weight in allocation_targets(profile).items())
        readiness = self.settings.readiness()
        available_specialists = [
            name
            for name in ("claude", "cursor", "gemini", "grok", "perplexity")
            if readiness.get(name, False) and name != self.provider.value
        ]
        request = {
            "task_id": record.task_id,
            "plan_generation": record.plan_generation,
            "owner_objective": record.intake.text,
            "execution_identity": {
                "acting_project_director": "claude",
                "provider": self.provider.value,
                "model": self.model_name,
                "transport": self.transport.value,
            },
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
            "allocation_profile": profile,
            "allocation_targets": dict(allocation_targets(profile)),
            "routing_rules": [
                (
                    "OpenAI planning is this Director call and OpenAI acceptance is the runtime review after specialist execution. Never emit an openai assignment. When the owner describes OpenAI plan -> Claude work -> Cursor review -> OpenAI acceptance, emit only the Claude and Cursor specialist assignments in that order; the runtime supplies the surrounding OpenAI phases."
                    if self.provider == ProviderName.OPENAI else
                    "Claude planning is this Director call and Claude acceptance is the runtime review after specialist execution. Never emit a claude assignment for this Director to accept. OpenAI's bounded engineering role is prospective here: the legacy worker registry still excludes openai, so never emit an openai assignment. Use only the available registered independent specialists or assign the owner when none can perform the work."
                ),
                "Set allow_draft_pr true only when the owner explicitly requested a draft pull request. Keep it false for analysis, review, research, and all other assignments. A true value also requires requires_approval true.",
                "Use estimated_cost_usd and max_task_budget_usd for the deterministic task-budget gate only. estimated_cost_usd is model-authored and is never trusted relative-cost evidence for provider selection. Provider-reported tokens or actual cost are optional telemetry: require them to be reported when exposed, but never make acceptance depend on telemetry the provider runtime does not expose.",
                "Treat OpenAI, Claude, Cursor, and Grok as the primary team. Grok is the Cursor-family peer and shares Cursor's full task-eligibility profile.",
                f"Never default medium or long work to Claude. For an unbound assignment, accept only the deterministic trusted selector result: an authenticated exact provider requirement has priority, then capability, configured readiness, allowlisted scope, exact-candidate reviewer independence, direct-production allocation for {profile}: {profile_weights} with plus-or-minus 3 point soft preference bands, then total-effort soft-preference rank, and finally trusted cheapest cost rank. Direct-production assignment counts above target plus 5 percentage points block routing in the prospective last-20 window; startup uses the full 20-slot denominator with real assignments only. Total-effort share supplies a soft ranking preference before cost, never a share-based eligibility blocker or accepted-production credit. Model-authored cost, capability, readiness, scope, provider requirements, or allocation claims are not routing authority.",
                "Never silently rewrite a bound provider assignment. If neither an authenticated exact provider requirement nor a trusted deterministic selection is supplied, assign the owner and ask one narrow routing question instead of guessing a cost order or defaulting to Claude.",
                "Route Cursor by verified capabilities, assignment authority, independence, capacity, and expected information gain across low through critical work. Do not reserve Cursor only for overflow or second passes.",
                f"Cursor may be assigned research, evidence verification, planning support, deep reasoning, elevated architecture and reliability work, complex implementation, QA, innovation, and leadership when those capabilities are available. It cannot replace {self.provider.value} runtime acceptance, mandatory Codex Security, or an independent non-author reviewer.",
                "Use Gemini Antigravity only for explicit bounded boilerplate, scaffolding, low-level mechanical implementation, or alternate ideation. Its output is non-authoritative evidence: it cannot create a draft pull request, perform exact-SHA review, final acceptance, or deterministic merge, or define contract or methodology truth. Never route generic prototype, browser, visual, core methodology, temporal eligibility, evaluation, provenance, or acceptance work to it by keyword alone.",
                "Every Antigravity work assignment must be followed by an independently assigned non-author verification assignment that directly depends on the artifact and either integrates it or rejects it.",
                "For ordinary Antigravity work no prose is transmitted. Its task and acceptance stay in the plan for your own review and the owner's Slack thread and are never transmitted, so dispatch is driven entirely by antigravity_directive: a lane from the closed fixture-lane enum, an optional language, and bounded variant_count and max_lines_per_artifact. Set antigravity_directive on every Antigravity work assignment, choosing a lane whose fixed public specification actually matches the bounded work; the runtime substitutes that specification. Leave repository, starting_ref, allow_draft_pr, and public_research_query unset. Set antigravity_directive to null for every other provider and every debate assignment.",
                "Antigravity cannot initiate, self-route, widen, or autonomously continue a conversation. Once you invite it through a persisted debate assignment it is equally eligible to receive the bounded question, the vote options, the accepted declared-dependency contributions, and one bounded revision request, and to respond, rebut, ask a focused follow-up, or abstain at its configured advisory weight. Its debate assignments must use kind general, discussion_round 0 for a position and 1 for a rebuttal, and must leave repository, starting_ref, allow_draft_pr, public_research_query, and antigravity_directive unset. A debate seat grants no review, acceptance, merge, deployment, publication, external-write, access, or methodology authority.",
                "Set kind on every assignment. Gemini work assignments permit only boilerplate, scaffolding, low_level_implementation, or abstract_ideation; Perplexity uses research; exact-SHA reviewers use exact_sha_review; use implementation or general otherwise.",
                "Use Perplexity only as a supplemental specialist when its distinct public-source research and sourcing strengths materially help.",
                "Route Grok by verified capabilities, assignment authority, independence, capacity, and expected information gain, including the same task-eligibility and advisory weight as Cursor when available. The Responses API contact is synchronous and tool-free; repository execution must use the separately authenticated Grok Build or Grok Bot surface in the named worktree. Transport limitations do not bypass reviewer-independence or self-review prohibitions.",
                "For medium, high, or critical complexity, prefer a bounded evidence-backed debate among available committee members before synthesis. Weighted voting is advisory only and cannot change authority or deterministic gates.",
                "A debate is a first-class runtime feature. For medium, high, or critical work, emit a debate whenever at least two of Claude, Cursor, Grok, and Antigravity are configured and the hop/budget limits permit it. Create independent position assignments using discussion_phase=position and discussion_round=0, then exactly one rebuttal assignment per participant using discussion_phase=rebuttal and discussion_round=1. Populate debate.position_assignments and debate.rebuttal_assignments exactly; each rebuttal must depend on every position and on no other rebuttal. Perplexity may supply a prior public evidence assignment but can never be a debate participant or voter.",
                "Count a producer's advisory vote at full configured weight while keeping that producer ineligible to review or accept its own work. Abstentions and unavailable members cast zero weight and never change the fixed configured denominator.",
                f"Resolve an ordinary advisory tie by documented {self.provider.value} evidence judgment. Ask the owner one focused question when tied options materially differ in cost, authority, safety, privacy, repository scope, runtime behavior, or project direction.",
                "Use each assignment's immutable retry_limit and token, cost, timeout, and cancellation budgets. Every additional provider attempt requires a classified failure, positive expected information gain, and new evidence, a corrected approach, or a materially different transport. Identical retries and provider-confirmation calls are prohibited. After three bounded recorded failures, mark the route unavailable for this assignment cycle, preserve partial output as untrusted evidence, and continue independent safe work. Never repeat an ambiguous paid dispatch; reconcile authenticated external-effect readback first.",
                "Pre-budget a different-provider review assignment for every requested draft PR or other reviewable implementation. The runtime binds its structured handoff to the logical producer, material authors, exact base/head SHA or artifact digest, changed files, tests, limitations, acceptance checks, and security context. A new commit invalidates prior review, Codex Security, CI, and merge evidence.",
                "Perplexity is evidence-only: give it methodological, theoretical, source-discovery, or fact-verification research without repository context, code work, review verdicts, votes, or external-write authority. Set requires_approval=true so the owner sees and approves the exact outbound query. Set public_research_query to a bounded public query containing no repository path or URL, commit SHA, source excerpt, private identifier, or secret-like value; set it null for every other provider.",
                "For owner-gated provider-UI coordination, use an already authenticated Chrome session first and Microsoft Edge only when Chrome is unavailable. Limit this to Claude, Cursor, Grok, or Gemini inside the stored assignment; never bypass authentication, consent, CAPTCHA, privacy, device, workspace-trust, permission, or security prompts, expose credentials or raw private source, change billing or access, post externally, merge, deploy, purchase, or treat browser output as authenticated provider or gate evidence. Perplexity remains API-only.",
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
            "task_id": record.task_id,
            "plan_generation": record.plan_generation,
            "plan_context": {
                "project": record.plan.project if record.plan else None,
                "normalized_outcome": record.plan.normalized_outcome if record.plan else None,
                "max_hops": record.plan.max_hops if record.plan else 0,
                "max_revisions": record.plan.max_revisions if record.plan else 0,
                "configured_max_task_budget_usd": self.settings.max_task_budget_usd,
                "estimated_total_cost_usd": (
                    estimated_plan_cost_usd(record.plan)
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
                "confidence, and exactly one vote_option from the debate plan or abstain=true. For "
                "a POSITION contribution, also require an explicit rationale, assumptions, material "
                "risks (an empty list is allowed), unknowns, and conditions that would change the "
                "position. Do not fabricate these fields; reject or escalate when the provider did "
                "not supply enough evidence. A REBUTTAL may leave those five fields null. "
                "Do not copy secrets, raw private source, or unrelated project context into it."
                if current_assignment
                and current_assignment.discussion_phase != DiscussionPhase.WORK
                else " Set debate_contribution to null for ordinary work assignments."
            ),
        }
        return await self._structured(json.dumps(request), ReviewDecision, "review_decision")

    async def counter_bias_debate(self, record: TaskRecord) -> DebateCounterBias:
        if record.plan is None or record.plan.debate is None:
            raise ValueError("counter-bias pass requires a debate plan")
        debate = record.plan.debate
        request = {
            "task_id": record.task_id,
            "plan_generation": record.plan_generation,
            "project": record.plan.project,
            "question": debate.question,
            "neutral_framing": debate.neutral_framing,
            "vote_options": debate.vote_options,
            "alternative_option": debate.alternative_option,
            "openai_initial_position": (
                debate.openai_initial_position.model_dump(mode="json")
                if debate.openai_initial_position is not None
                else None
            ),
            "specialist_turns": [
                turn.model_dump(mode="json")
                for turn in record.collaboration_turns
                if turn.phase in {"position", "rebuttal"}
                and turn.assignment_sequence is not None
            ],
            "instruction": (
                f"Act only as {self.provider.value} Moderator. Before any final synthesis or {self.provider.value} vote, "
                "challenge the preliminary view's assumptions, state the strongest "
                "counterargument, identify disconfirming evidence, reconsider the configured "
                "options and any designated alternative, assess independence from anchoring and "
                "provider imitation, and state whether the provisional recommendation changed. "
                "Do not cast a vote in this pass, choose a tie winner, authorize an action, or "
                "invent missing participation. Withholding the vote here only keeps this pass from "
                f"pre-empting synthesis; the single {self.provider.value} vote is still cast in full "
                "at synthesis unless you explicitly abstain. Moderation never removes that vote. "
                "Return only the structured counter-bias result."
            ),
        }
        return await self._structured(
            json.dumps(request), DebateCounterBias, "debate_counter_bias"
        )

    async def synthesize_debate(
        self,
        record: TaskRecord,
        counter_bias: DebateCounterBias,
        *,
        counter_bias_interaction_id: str,
        counter_bias_event_digest: str,
    ) -> DebateSynthesis:
        if record.plan is None or record.plan.debate is None:
            raise ValueError("debate synthesis requires a debate plan")
        request = {
            "task_id": record.task_id,
            "plan_generation": record.plan_generation,
            "project": record.plan.project,
            "question": record.plan.debate.question,
            "vote_options": record.plan.debate.vote_options,
            "material_risk": record.plan.debate.material_risk,
            "material_dimensions": record.plan.debate.material_dimensions,
            "tie_judgment_rubric": (
                record.plan.debate.tie_judgment_rubric.model_dump(mode="json")
                if record.plan.debate.tie_judgment_rubric is not None
                else None
            ),
            "verified_counter_bias": counter_bias.model_dump(mode="json"),
            "counter_bias_interaction_id": counter_bias_interaction_id,
            "counter_bias_verified_event_digest": counter_bias_event_digest,
            "turns": [
                turn.model_dump(mode="json")
                for turn in record.collaboration_turns
                if turn.phase in {"position", "rebuttal"}
            ],
            "unavailable_providers": [item.value for item in record.unavailable_providers],
            "availability_impacts": record.availability_impacts,
            "instruction": (
                "Synthesize the bounded debate using the separately completed, verified counter-bias "
                "result without inventing missing evidence. Preserve meaningful dissent and select "
                "exactly one configured vote option or abstain. Your vote is advisory. Do not perform "
                "or claim a tie judgment in this response; deterministic tallying occurs afterward. "
                "Do not authorize merge, deployment, publication, spending, access, or any other action. "
                "State one bounded next action. A material tie is handled deterministically after this call."
            ),
        }
        return await self._structured(
            json.dumps(request), DebateSynthesis, "debate_synthesis"
        )

    async def judge_debate_tie(
        self,
        record: TaskRecord,
        *,
        winning_options: list[str],
        synthesis_interaction_id: str,
        synthesis_event_digest: str,
    ) -> DebateTieJudgment:
        if record.plan is None or record.plan.debate is None:
            raise ValueError("tie judgment requires a debate plan")
        rubric = record.plan.debate.tie_judgment_rubric
        if rubric is None:
            raise ValueError("tie judgment requires a predeclared rubric")
        request = {
            "task_id": record.task_id,
            "plan_generation": record.plan_generation,
            "project": record.plan.project,
            "question": record.plan.debate.question,
            "tied_winning_options": winning_options,
            "predeclared_rubric": rubric.model_dump(mode="json"),
            "verified_synthesis": record.debate_synthesis.model_dump(mode="json"),
            "synthesis_interaction_id": synthesis_interaction_id,
            "synthesis_verified_event_digest": synthesis_event_digest,
            "instruction": (
                f"Act only as the non-voting {self.provider.value} Director. Apply every predeclared rubric "
                "criterion once and in order, cite a concrete finding for each, and choose only "
                "from the deterministic tied_winning_options. Do not cast another vote, change "
                "the tally, invent participation, or authorize an external action."
            ),
        }
        return await self._structured(
            json.dumps(request), DebateTieJudgment, "debate_tie_judgment"
        )


def build_director(settings: Settings, *, claude_transport=None):
    """Select execution explicitly; project leadership never implies activation."""
    if not settings.claude_director_enabled:
        return OpenAIDirector(settings)
    from .claude_director import ClaudeDirector, ClaudeDirectorPacketTransport

    if not isinstance(claude_transport, ClaudeDirectorPacketTransport):
        raise RuntimeError("Claude Director requires an explicitly enrolled native packet transport")
    return ClaudeDirector(settings, claude_transport)


# [[Codex | GPT-5.6 | 2026-08-24 | 13:34 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 14:01 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 22:11 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:18 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | END]]

# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:23 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:00 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 10:40 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 19:11 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:30 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:21 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:38 UTC | END]]

# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:56 UTC | END]]
