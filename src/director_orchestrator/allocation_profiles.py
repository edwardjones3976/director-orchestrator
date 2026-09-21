# [[Claude Code | claude-sonnet-5 | 2026-09-21 | 21:56 UTC | BEGIN]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 21:30 UTC | BEGIN]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 20:49 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:29 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 05:57 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 01:02 UTC | BEGIN]]
"""Owner-approved allocation authority; subscription multipliers are capacity signals."""
from types import MappingProxyType
import hashlib
import json

DEFAULT_ALLOCATION_PROFILE = "engineering"
LANES = ("openai", "claude", "cursor", "grok", "antigravity", "perplexity", "firecrawl", "github-copilot")
ALLOCATION_PROFILES = MappingProxyType({
    name: MappingProxyType(dict(zip(LANES, weights, strict=True)))
    for name, weights in {
        "mixed": (27,33,10,10,5,5,5,5),
        "engineering": (40,25,15,10,10,0,0,0),
        "public_research": (20,25,5,5,5,25,15,0),
        "enterprise": (32,38,5,5,5,0,5,10),
    }.items()
})

def allocation_targets(profile: str = DEFAULT_ALLOCATION_PROFILE):
    if not isinstance(profile, str) or profile not in ALLOCATION_PROFILES:
        raise ValueError("allocation_profile_unknown")
    return ALLOCATION_PROFILES[profile]

# Read-only history provenance from this module at 6c09a1e1aba8921464fa3564cdeb00b34ce1b962,
# before the owner's prospective role swap. Shared by every reader (Postgres primary
# workload and the standalone SQLite engineering ledger) so neither imports the other's
# internals. These identities never authorize a new reservation or replace an existing
# row's immutable admission binding.
PRE_RETIREMENT_V3 = MappingProxyType({
    "engineering": ("sha256:84a77ab961f256ab03384b8474bb5c903c04d7248c6b0bb0b74bf79c2b1c6219",
                    frozenset({"openai", "claude", "cursor", "grok", "antigravity"})),
    "mixed": ("sha256:8d52b872f98a03b893e87f53e607ddf8525092f647c7179baa23732f55fe94f3",
              frozenset({"openai", "claude", "cursor", "grok", "antigravity", "perplexity", "firecrawl", "github-copilot", "microsoft-365-copilot"})),
    "public_research": ("sha256:bc543fafca4d720f6260fbee218600a1d8b88f93d0d73ae19b34040bac522a5b",
                        frozenset({"openai", "claude", "cursor", "grok", "antigravity", "perplexity", "firecrawl"})),
    "enterprise": ("sha256:7dc8522cf2d607d150d71924bb0a6eabe2b4ef1312f70942924bd74349e47313",
                   frozenset({"openai", "claude", "cursor", "grok", "antigravity", "firecrawl", "github-copilot", "microsoft-365-copilot"})),
})

def historical_allocation_profile_digest(profile: str = DEFAULT_ALLOCATION_PROFILE) -> str:
    """Pinned original hard_tolerance5 identity, retired 2026-09-21 by the owner.

    Verification only: authenticates rows admitted before the retirement. It
    never authorizes a new reservation and is never bound to new admissions.
    """
    payload = {"version": "capacity-allocation-v1", "profile": profile,
               "targets": dict(allocation_targets(profile)), "window": 20,
               "soft_tolerance": 3, "hard_tolerance": 5,
               "unit": "admitted_assignment", "zero_target": "ineligible"}
    return "sha256:" + hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def allocation_profile_digest(profile: str = DEFAULT_ALLOCATION_PROFILE) -> str:
    """Current prospective identity; every live admission caller binds this value."""
    payload = {"version": "capacity-allocation-v2-prospective", "profile": profile,
               "targets": dict(allocation_targets(profile)), "window": 20,
               "soft_tolerance": 3,
               "unit": "admitted_assignment", "zero_target": "ineligible"}
    return "sha256:" + hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def prospective_allocation_profile_digest(profile: str = DEFAULT_ALLOCATION_PROFILE) -> str:
    return allocation_profile_digest(profile)

def canonical_allocation_lane(lane: str) -> str:
    return {"external_openai": "openai", "gemini": "antigravity"}.get(lane, lane)
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 01:02 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 05:57 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:29 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | END]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 20:49 UTC | END]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 21:30 UTC | END]]
# [[Claude Code | claude-sonnet-5 | 2026-09-21 | 21:56 UTC | END]]
