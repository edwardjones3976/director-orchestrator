# [[Claude Code | claude-sonnet-5 | 2026-09-21 | 22:52 UTC | BEGIN]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 20:49 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 10:40 UTC | BEGIN]]
# [[Gemini Antigravity | Gemini 3.8 Flash (Medium) | 2026-09-13 | 10:30 UTC | BEGIN]]
# [[Gemini Antigravity | Gemini 3.8 Flash (High) | 2026-09-13 | 10:25 UTC | BEGIN]]
"""Conditional accepted-production allocation profile descriptor (Issue 207).

This module defines an immutable, versioned, PROPOSED static descriptor for the
eleven-lane conditional allocation policy (20/18/13/13/13/10/5/3/2/2/1):
    Antigravity: 20%
    Z.ai: 18%
    Cursor: 13%
    Direct Grok: 13%
    Grok Bot: 13%
    OpenAI production: 10%
    Claude: 5%
    Perplexity: 3%
    GitHub Copilot: 2%
    VS Code Coding Bot: 2%
    Firecrawl: 1%
Total: 100%

Key architectural constraints:
1. Proposed only: This profile confers no runtime readiness, admission authority,
   or enrollment. Active profiles in `allocation_profiles.py` remain authoritative.
2. Descriptive prerequisites: Conditional activation requires passing on/after
   2026-09-29 UTC, accepted Z.ai Lite qualification evidence, separate explicit
   owner activation approval, plus all standard runtime/enrollment/review/security/CI gates.
   Prerequisites are named immutable requirements, not runtime status flags.
3. Trial boundary: Z.ai Lite trial through 2026-09-28 earns no production credit.
   No automatic Max purchase, activation, or quota redistribution.
4. Policy families: direct Grok and Grok Bot share the "xAI" policy family;
   GitHub Copilot and VS Code Coding Bot share the "GitHub/Microsoft" policy family.
   These are owner-selected policy labels for reviewer-independence and anti-duplication
   restrictions, not proof of a managed serving model. Different family labels
   never prove reviewer independence, and no admission/independence engine is exposed.
5. Unresolved integration: VS Code Coding Bot integration is unresolved; no route
   is invented. All contacts remain unbound.
6. Distinct OpenAI ledgers: The 75% OpenAI Director-activity floor applies to
   OpenAI's own authenticated activity (control-plane effort) and has a distinct
   denominator from the 10% direct-production target. This descriptor does not
   implement or enforce the ledgers; runtime ledgers remain required.
7. Specialty boundaries: Perplexity (public research) and Firecrawl (authorized
   retrieval) retain bounded roles. No permanent specialties exist for other lanes.
8. Soft/Hard tolerance: Window of 20 slots (5% per slot). Normal tolerance is +/-3
   percentage points (preference). `hard_ceiling`/`hard_tolerance_percentage_points`
   retain the target + 5 point historical reference number only; the owner retired
   that rolling-twenty hard-ceiling enforcement mechanism on 2026-09-21 as outdated.
   This staged descriptor was never bound to any runtime cap, so nothing here was
   removed or newly enforced; the fields are reference numbers only. For Firecrawl
   (target 1%), a 5% mathematical slot is within that reference ceiling (6%), but
   this descriptor confers no actual admission or execution authority.
9. Canonical digest: All semantic descriptor fields (prerequisites, dates, OpenAI
   floor, denominators, contacts, boundaries) are bound in one canonical payload,
   and the digest is derived directly from that payload.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

CONDITIONAL_PROFILE_VERSION = "capacity-allocation-conditional-v1"
CONDITIONAL_PROFILE_NAME = "conditional_20260929"
CONDITIONAL_PROFILE_STATUS = "PROPOSED"
CONDITIONAL_EFFECTIVE_DATE_FLOOR = "2026-09-29"

CONDITIONAL_WINDOW = 20
CONDITIONAL_SLOT_PERCENTAGE = 5
CONDITIONAL_SOFT_TOLERANCE = 3
CONDITIONAL_HARD_TOLERANCE = 5

OPENAI_DIRECTOR_ACTIVITY_FLOOR_PERCENT = 75
OPENAI_DIRECT_PRODUCTION_TARGET_PERCENT = 10
OPENAI_DIRECTOR_FLOOR_DENOMINATOR = "authenticated_openai_activity"
OPENAI_PRODUCTION_TARGET_DENOMINATOR = "global_accepted_production"

CONDITIONAL_LANES: tuple[str, ...] = (
    "antigravity",
    "zai",
    "cursor",
    "grok",
    "grok-bot",
    "openai",
    "claude",
    "perplexity",
    "github-copilot",
    "vscode-coding-bot",
    "firecrawl",
)

CONDITIONAL_TARGETS: MappingProxyType[str, int] = MappingProxyType({
    "antigravity": 20,
    "zai": 18,
    "cursor": 13,
    "grok": 13,
    "grok-bot": 13,
    "openai": 10,
    "claude": 5,
    "perplexity": 3,
    "github-copilot": 2,
    "vscode-coding-bot": 2,
    "firecrawl": 1,
})

CONDITIONAL_POLICY_FAMILIES: MappingProxyType[str, str] = MappingProxyType({
    "antigravity": "Google",
    "zai": "Z.ai",
    "cursor": "Anysphere",
    "grok": "xAI",
    "grok-bot": "xAI",
    "openai": "OpenAI",
    "claude": "Anthropic",
    "perplexity": "Perplexity",
    "github-copilot": "GitHub/Microsoft",
    "vscode-coding-bot": "GitHub/Microsoft",
    "firecrawl": "Firecrawl",
})

RESEARCH_SPECIALTY_BOUNDARIES: MappingProxyType[str, str] = MappingProxyType({
    "perplexity": "public_research_evidence_only",
    "firecrawl": "authorized_public_retrieval_only",
})


@dataclass(frozen=True)
class ConditionalPrerequisite:
    """Named immutable requirement for conditional profile activation."""

    name: str
    requirement: str


CONDITIONAL_PREREQUISITES: tuple[ConditionalPrerequisite, ...] = (
    ConditionalPrerequisite(
        name="effective_date_boundary",
        requirement=(
            "Activation on or after 2026-09-29 UTC. Z.ai trial through 2026-09-28 "
            "earns no accepted-production credit."
        ),
    ),
    ConditionalPrerequisite(
        name="owner_zai_qualification_acceptance",
        requirement=(
            "Owner evaluation and acceptance of Z.ai Lite trial evidence "
            "(correctness, first-pass yield, correction rate, latency, cost, privacy fit)."
        ),
    ),
    ConditionalPrerequisite(
        name="owner_activation_and_subscription_approval",
        requirement=(
            "Separate explicit owner decision approving profile activation and any subscription "
            "purchase; no automatic Max purchase, activation, or quota redistribution."
        ),
    ),
    ConditionalPrerequisite(
        name="deterministic_safety_and_ci_gates",
        requirement=(
            "All deterministic admission, exact-candidate review, Codex Security, "
            "and trusted CI gates remain mandatory."
        ),
    ),
    ConditionalPrerequisite(
        name="dual_activity_ledgers_operational",
        requirement=(
            "Operational dual activity ledgers separating OpenAI Director control plane "
            "(>=75% floor of authenticated OpenAI activity) from direct production (10% target). "
            "Runtime ledgers and consumer integration are not implemented here and remain required."
        ),
    ),
    ConditionalPrerequisite(
        name="vscode_coding_bot_integration_resolved",
        requirement=(
            "Installed independent integration must be identified and verified before binding; "
            "unresolved identity remains unbound with no route invented."
        ),
    ),
)


@dataclass(frozen=True)
class ConditionalContactDescriptor:
    """Typed contact/product and policy-family descriptor for a provider lane."""

    lane_id: str
    product_name: str
    display_name: str
    target_percent: int
    policy_family: str
    is_resolved: bool
    binding_status: str
    route_binding: str | None
    boundary_note: str | None
    soft_tolerance_band: tuple[int, int]
    hard_ceiling: int  # Historical reference number only; not an enforced runtime cap.


def _build_contacts() -> MappingProxyType[str, ConditionalContactDescriptor]:
    display_names = {
        "antigravity": "Gemini / Antigravity",
        "zai": "Z.ai",
        "cursor": "Cursor",
        "grok": "Direct Grok",
        "grok-bot": "Grok Bot",
        "openai": "OpenAI / Codex production",
        "claude": "Claude",
        "perplexity": "Perplexity",
        "github-copilot": "GitHub Copilot",
        "vscode-coding-bot": "VS Code Coding Bot",
        "firecrawl": "Firecrawl",
    }
    product_names = {
        "antigravity": "Antigravity",
        "zai": "Z.ai Lite",
        "cursor": "Cursor",
        "grok": "Direct Grok",
        "grok-bot": "Grok Bot",
        "openai": "OpenAI / Codex production",
        "claude": "Claude",
        "perplexity": "Perplexity",
        "github-copilot": "GitHub Copilot",
        "vscode-coding-bot": "VS Code Coding Bot",
        "firecrawl": "Firecrawl",
    }
    boundary_notes = {
        "perplexity": "Public research evidence only; no private repository context",
        "firecrawl": "Authorized public retrieval only",
        "openai": "10% direct-production target separate from authenticated OpenAI Director floor (>=75%)",
        "zai": "Trial-only through 2026-09-28; earns no production credit without 2026-09-29 owner activation",
        "grok": "Shared xAI policy family for independence, review, debate, and anti-duplication",
        "grok-bot": "Shared xAI policy family for independence, review, debate, and anti-duplication",
        "github-copilot": "Shared GitHub/Microsoft policy family for independence and anti-duplication",
        "vscode-coding-bot": "Installed independent integration not identified; identity and binding unresolved; no route invented",
    }

    contacts: dict[str, ConditionalContactDescriptor] = {}
    for lane in CONDITIONAL_LANES:
        target = CONDITIONAL_TARGETS[lane]
        family = CONDITIONAL_POLICY_FAMILIES[lane]
        is_resolved = (lane != "vscode-coding-bot")
        contacts[lane] = ConditionalContactDescriptor(
            lane_id=lane,
            product_name=product_names[lane],
            display_name=display_names[lane],
            target_percent=target,
            policy_family=family,
            is_resolved=is_resolved,
            binding_status="unresolved" if not is_resolved else "unbound",
            route_binding=None,
            boundary_note=boundary_notes.get(lane),
            soft_tolerance_band=(max(0, target - CONDITIONAL_SOFT_TOLERANCE), target + CONDITIONAL_SOFT_TOLERANCE),
            hard_ceiling=target + CONDITIONAL_HARD_TOLERANCE,
        )
    return MappingProxyType(contacts)


CONDITIONAL_CONTACTS: MappingProxyType[str, ConditionalContactDescriptor] = _build_contacts()


@dataclass(frozen=True)
class ConditionalProfileDescriptor:
    """Immutable static descriptor for the proposed conditional allocation profile.

    Derives its canonical digest dynamically from all semantic fields so the digest
    never becomes stale if the descriptor is reconstructed or replaced.
    """

    name: str = CONDITIONAL_PROFILE_NAME
    version: str = CONDITIONAL_PROFILE_VERSION
    status: str = CONDITIONAL_PROFILE_STATUS
    effective_date_floor: str = CONDITIONAL_EFFECTIVE_DATE_FLOOR
    window: int = CONDITIONAL_WINDOW
    slot_percentage: int = CONDITIONAL_SLOT_PERCENTAGE
    soft_tolerance_percentage_points: int = CONDITIONAL_SOFT_TOLERANCE
    hard_tolerance_percentage_points: int = CONDITIONAL_HARD_TOLERANCE
    unit: str = "admitted_assignment"
    zero_target_rule: str = "ineligible"
    confers_runtime_readiness: bool = False
    confers_enrollment: bool = False
    is_bound: bool = False
    openai_director_activity_floor_percent: int = OPENAI_DIRECTOR_ACTIVITY_FLOOR_PERCENT
    openai_director_floor_denominator: str = OPENAI_DIRECTOR_FLOOR_DENOMINATOR
    openai_direct_production_target_percent: int = OPENAI_DIRECT_PRODUCTION_TARGET_PERCENT
    openai_production_target_denominator: str = OPENAI_PRODUCTION_TARGET_DENOMINATOR
    targets: MappingProxyType[str, int] = CONDITIONAL_TARGETS
    policy_families: MappingProxyType[str, str] = CONDITIONAL_POLICY_FAMILIES
    contacts: MappingProxyType[str, ConditionalContactDescriptor] = CONDITIONAL_CONTACTS
    prerequisites: tuple[ConditionalPrerequisite, ...] = CONDITIONAL_PREREQUISITES
    research_specialty_boundaries: MappingProxyType[str, str] = RESEARCH_SPECIALTY_BOUNDARIES

    @property
    def canonical_payload(self) -> dict[str, Any]:
        """Produce the canonical, deterministic dictionary binding all semantic fields."""
        return {
            "confers_enrollment": self.confers_enrollment,
            "confers_runtime_readiness": self.confers_runtime_readiness,
            "contacts": [
                {
                    "lookup_key": lookup_key,
                    "binding_status": c.binding_status,
                    "boundary_note": c.boundary_note,
                    "display_name": c.display_name,
                    "hard_ceiling": c.hard_ceiling,
                    "is_resolved": c.is_resolved,
                    "lane_id": c.lane_id,
                    "policy_family": c.policy_family,
                    "product_name": c.product_name,
                    "route_binding": c.route_binding,
                    "soft_tolerance_band": list(c.soft_tolerance_band),
                    "target_percent": c.target_percent,
                }
                for lookup_key, c in self.contacts.items()
            ],
            "effective_date_floor": self.effective_date_floor,
            "hard_tolerance_percentage_points": self.hard_tolerance_percentage_points,
            "is_bound": self.is_bound,
            "name": self.name,
            "openai_direct_production_target_percent": self.openai_direct_production_target_percent,
            "openai_director_activity_floor_percent": self.openai_director_activity_floor_percent,
            "openai_director_floor_denominator": self.openai_director_floor_denominator,
            "openai_production_target_denominator": self.openai_production_target_denominator,
            "policy_families": dict(self.policy_families),
            "prerequisites": [
                {"name": p.name, "requirement": p.requirement}
                for p in self.prerequisites
            ],
            "research_specialty_boundaries": dict(self.research_specialty_boundaries),
            "slot_percentage": self.slot_percentage,
            "soft_tolerance_percentage_points": self.soft_tolerance_percentage_points,
            "status": self.status,
            "targets": dict(self.targets),
            "unit": self.unit,
            "version": self.version,
            "window": self.window,
            "zero_target_rule": self.zero_target_rule,
        }

    @property
    def digest(self) -> str:
        """Derive the deterministic canonical digest from the semantic payload."""
        encoded = json.dumps(self.canonical_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    def get_contact(self, lane: str) -> ConditionalContactDescriptor:
        if lane not in self.contacts:
            raise KeyError(f"Unknown conditional lane: {lane}")
        return self.contacts[lane]

    def policy_family_for(self, lane: str) -> str:
        """Return the policy family for a known lane; raises KeyError on unknown lane."""
        if lane not in self.policy_families:
            raise KeyError(f"Unknown conditional lane: {lane}")
        return self.policy_families[lane]

    def shares_policy_family(self, lane_a: str, lane_b: str) -> bool:
        """Check if two known lanes share a policy family. Raises KeyError if unknown.

        Sharing a family restricts reviewer independence and duplicate credit.
        Having different family labels does NOT prove reviewer independence.
        """
        return self.policy_family_for(lane_a) == self.policy_family_for(lane_b)

    def unresolved_lanes(self) -> tuple[str, ...]:
        return tuple(lane for lane, c in self.contacts.items() if not c.is_resolved)


CONDITIONAL_PROFILE = ConditionalProfileDescriptor()
CONDITIONAL_PROFILE_DIGEST: str = CONDITIONAL_PROFILE.digest


def get_conditional_profile() -> ConditionalProfileDescriptor:
    """Return the immutable conditional profile descriptor."""
    return CONDITIONAL_PROFILE


def shares_policy_family(lane_a: str, lane_b: str) -> bool:
    """Helper to check if two known lanes share a policy family; rejects unknown lanes."""
    return CONDITIONAL_PROFILE.shares_policy_family(lane_a, lane_b)
# [[Gemini Antigravity | Gemini 3.8 Flash (High) | 2026-09-13 | 10:25 UTC | END]]
# [[Gemini Antigravity | Gemini 3.8 Flash (Medium) | 2026-09-13 | 10:30 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 10:40 UTC | END]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 20:49 UTC | END]]
# [[Claude Code | claude-sonnet-5 | 2026-09-21 | 22:52 UTC | END]]
