# [[OpenAI Codex | GPT-6 | 2026-09-06 | 01:02 UTC | BEGIN]]
"""Owner-approved allocation authority; subscription multipliers are capacity signals."""
from types import MappingProxyType
import hashlib
import json

DEFAULT_ALLOCATION_PROFILE = "engineering"
LANES = ("openai", "claude", "cursor", "grok", "antigravity", "perplexity", "firecrawl", "github-copilot", "microsoft-365-copilot")
ALLOCATION_PROFILES = MappingProxyType({
    name: MappingProxyType(dict(zip(LANES, weights, strict=True)))
    for name, weights in {
        "mixed": (30,25,10,10,5,5,5,5,5),
        "engineering": (35,30,15,10,10,0,0,0,0),
        "public_research": (25,20,5,5,5,25,15,0,0),
        "enterprise": (30,25,5,5,5,0,5,10,15),
    }.items()
})

def allocation_targets(profile: str = DEFAULT_ALLOCATION_PROFILE):
    if not isinstance(profile, str) or profile not in ALLOCATION_PROFILES:
        raise ValueError("allocation_profile_unknown")
    return ALLOCATION_PROFILES[profile]

def allocation_profile_digest(profile: str = DEFAULT_ALLOCATION_PROFILE) -> str:
    payload = {"version": "capacity-allocation-v1", "profile": profile,
               "targets": dict(allocation_targets(profile)), "window": 20,
               "soft_tolerance": 3, "hard_tolerance": 5,
               "unit": "admitted_assignment", "zero_target": "ineligible"}
    return "sha256:" + hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def canonical_allocation_lane(lane: str) -> str:
    return {"external_openai": "openai", "gemini": "antigravity"}.get(lane, lane)
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 01:02 UTC | END]]
