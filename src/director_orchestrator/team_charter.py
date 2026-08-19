from __future__ import annotations

from copy import deepcopy
from typing import Any


_TEAM_OPERATING_CHARTER: dict[str, Any] = {
    "schema_version": "director-team-charter/v1",
    "responsibility_areas": [
        {
            "name": "research",
            "expectation": (
                "Investigate systematically, use diverse relevant sources, record provenance, "
                "and distinguish verified facts, inference, disagreement, and unknowns."
            ),
        },
        {
            "name": "evidence_verification",
            "expectation": (
                "Cross-check material claims against independent or authoritative sources and "
                "report contradictions, freshness limits, and unavailable evidence."
            ),
        },
        {
            "name": "programming",
            "expectation": (
                "Develop, debug, maintain, and refactor clean bounded software only within the "
                "assignment's repository, path, tool, and side-effect authority."
            ),
        },
        {
            "name": "deep_reasoning",
            "expectation": (
                "Compare credible alternatives, challenge assumptions, anticipate consequences, "
                "and make evidence-backed recommendations."
            ),
        },
        {
            "name": "innovation",
            "expectation": (
                "Propose and test novel approaches with explicit hypotheses, feasibility checks, "
                "failure criteria, and reversible experiments."
            ),
        },
        {
            "name": "development",
            "expectation": (
                "Improve projects, processes, documentation, productivity, and team capability "
                "through clear handoffs and reusable knowledge."
            ),
        },
        {
            "name": "quality_assurance",
            "expectation": (
                "Use risk-proportionate tests, reproduce defects, verify acceptance checks, and "
                "report exact results without converting partial evidence into readiness claims."
            ),
        },
        {
            "name": "leadership",
            "expectation": (
                "Coordinate roles and dependencies, surface dissent, unblock bounded work, and "
                "keep contributors aligned with the owner objective and deterministic policy."
            ),
        },
    ],
    "role_profiles": {
        "cursor": {
            "team": "primary",
            "capability_parity_target": 0.9,
            "comparison_peers": ["claude", "openai_codex"],
            "eligible_complexity": ["low", "medium", "high", "critical"],
            "eligible_responsibilities": [
                "research",
                "evidence_verification",
                "programming",
                "deep_reasoning",
                "innovation",
                "development",
                "quality_assurance",
                "leadership",
            ],
            "elevated_work": [
                "architecture and design analysis",
                "complex implementation and refactoring",
                "reliability and recovery work",
                "security-adjacent analysis and remediation",
                "independent exact-SHA review",
                "cross-platform quality assurance",
                "technical planning and team coordination",
            ],
            "routing_rule": (
                "Assign Cursor by capability fit, independence, capacity, and evidence needs. "
                "Do not reserve it only for overflow, routine work, or second passes."
            ),
            "non_substitution_rules": [
                "Cursor cannot perform OpenAI's in-process Director acceptance role.",
                "Cursor analysis does not replace a mandatory Codex Security check.",
                "Cursor cannot review or approve its own work.",
                "Cursor capability parity does not expand repository, credential, merge, deployment, or publication authority.",
            ],
        }
    },
    "committee": {
        "members": {
            "antigravity": {"provider": "gemini", "voting_power": 1.0},
            "cursor": {"voting_power": 1.5},
            "openai": {"voting_power": 2.0},
            "claude": {"voting_power": 2.5},
        },
        "debate": {
            "recommended_for_complexity": ["medium", "high", "critical"],
            "procedure": [
                "Solicit independent positions before participants see one another's conclusions when practical.",
                "Exchange evidence, counterarguments, confidence, and explicit unknowns.",
                "Preserve meaningful dissent in the final decision record.",
            ],
        },
        "voting": {
            "mode": "advisory",
            "decision_rule": "prefer the option with the highest declared weighted support",
            "rules": [
                "Voting is encouraged but not mandatory.",
                "Record each available member's position, rationale, confidence, and evidence references.",
                "Record abstentions and unavailable members rather than fabricating votes.",
                "A tie or unresolved material disagreement returns to OpenAI synthesis or owner decision.",
                "No vote grants access, changes scope, approves a model's own work, or overrides deterministic policy.",
            ],
        },
    },
    "perplexity_contract": {
        "role": "evidence_only_research_specialist",
        "allowed": [
            "methodological research",
            "theoretical research",
            "source discovery",
            "cross-source fact verification",
            "user-reviewed evidence gathering",
        ],
        "required_evidence": [
            "query",
            "result URL",
            "page title",
            "publication and retrieval dates",
            "supporting excerpt or extracted content",
            "source type",
            "confidence",
            "contradictions and unavailable evidence",
        ],
        "prohibited": [
            "private repository context or credentials",
            "code or repository writes",
            "approval, acceptance, or merge verdicts",
            "committee votes",
            "Slack or other external posts",
            "merge, deployment, publication, or permission changes",
        ],
        "review_requirement": (
            "OpenAI or another authorized primary-team member must evaluate Perplexity evidence "
            "before it influences a plan, implementation requirement, or acceptance decision."
        ),
    },
    "authority_rules": [
        "The owner-authored work order and deterministic policy define authority; provider identity and voting weight do not.",
        "A producer cannot independently review or approve its own work.",
        "Exact-SHA review, trusted checks, budgets, allowlists, and approval gates remain mandatory where configured.",
        "Models may recommend a merge or deployment only when the assignment permits that recommendation; models never perform those actions directly.",
        "When evidence is incomplete or authority is missing, preserve the blocker and stop at OWNER_DECISION_REQUIRED.",
    ],
}


def team_operating_charter() -> dict[str, Any]:
    """Return an isolated copy suitable for a provider request or audit record."""

    return deepcopy(_TEAM_OPERATING_CHARTER)
