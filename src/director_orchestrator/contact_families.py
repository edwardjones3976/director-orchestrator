# [[Claude Code | Claude Fable 5.1 | 2026-09-20 | 03:40 UTC | BEGIN]]
# [[GitHub Copilot CLI | Claude Sonnet 5 | 2026-09-20 | 03:01 UTC | BEGIN]]
"""Typed contact-lane and provider-family identities (owner directive #207).

Directive #207, "Contact-family safety", rules that separate contact lanes
never create false different-provider review independence or duplicate
accepted credit when they are, in fact, the same underlying provider family.
Direct Grok and Grok Bot are one xAI family; GitHub Copilot and VS Code
Coding Bot are one GitHub/Microsoft family; Gemini and Antigravity are one
Google family; and so on for the remaining contacts below.

The ``provider_family`` labels in this module are owner-selected policy
labels used only for reviewer-independence and anti-duplication decisions.
They are NOT authenticated evidence of which underlying model actually
served any particular request; nothing here attests live provider routing.

The table is additive and deliberately does not grow ``ProviderName``: a
contact lane may exist here (for example ``grok-bot`` or
``vscode-coding-bot``) with no corresponding enum member and no route. Any
identity absent from the table is unknown and therefore never independent;
every lookup fails closed via ``UnknownContactError``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from .models import ProviderName

ContactIdentity = Union[ProviderName, str]


@dataclass(frozen=True)
class ContactLane:
    """A typed contact-lane identity: a contact/lane id, its product, and its family."""

    lane_id: str
    product: str
    provider_family: str


class UnknownContactError(ValueError):
    """Raised when an identity has no recorded contact-lane entry.

    Unknown identities are never treated as independent of anything: every
    caller must fail closed rather than assume distinct families.
    """


# Every known contact and lane id, as strings. Adding a lane here never grants
# it a route, a route status, or standing authority; see
# docs/provider-allocation-profiles.md and docs/project-leadership.md for that.
_CONTACT_LANES: tuple[ContactLane, ...] = (
    # The owner is a ProviderName member (human approval steps), never a provider
    # family: it shares a family with nobody and must not fail closed as unknown.
    ContactLane(lane_id="owner", product="Owner", provider_family="Owner"),
    ContactLane(lane_id="openai", product="ChatGPT / Codex", provider_family="OpenAI"),
    ContactLane(lane_id="claude", product="Claude Code CLI", provider_family="Anthropic"),
    ContactLane(lane_id="cursor", product="Cursor Agent", provider_family="Anysphere"),
    ContactLane(lane_id="grok", product="Direct Grok", provider_family="xAI"),
    ContactLane(lane_id="grok-bot", product="Grok Bot", provider_family="xAI"),
    ContactLane(lane_id="gemini", product="Gemini", provider_family="Google"),
    ContactLane(lane_id="antigravity", product="Antigravity", provider_family="Google"),
    ContactLane(lane_id="perplexity", product="Perplexity", provider_family="Perplexity"),
    ContactLane(lane_id="firecrawl", product="Firecrawl", provider_family="Firecrawl"),
    ContactLane(
        lane_id="github-copilot",
        product="GitHub Copilot CLI",
        provider_family="GitHub/Microsoft",
    ),
    # Unresolved contact: no route. Kept typed so it can never be silently
    # treated as independent of github-copilot merely by having a different id.
    ContactLane(
        lane_id="vscode-coding-bot",
        product="VS Code Coding Bot",
        provider_family="GitHub/Microsoft",
    ),
    ContactLane(lane_id="zai", product="Z.ai (GLM Coding Plan)", provider_family="Z.ai"),
)

CONTACT_LANES_BY_ID: dict[str, ContactLane] = {lane.lane_id: lane for lane in _CONTACT_LANES}


def _identity_string(identity: ContactIdentity) -> str:
    if isinstance(identity, ProviderName):
        return identity.value
    return str(identity)


def contact_lane_for(identity: ContactIdentity) -> ContactLane:
    """Return the typed ``ContactLane`` for ``identity``; fail closed if unknown."""

    key = _identity_string(identity)
    try:
        return CONTACT_LANES_BY_ID[key]
    except KeyError as exc:
        raise UnknownContactError(f"unknown contact identity: {key!r}") from exc


def provider_family_for(identity: ContactIdentity) -> str:
    """Return the provider-family label for ``identity``; fail closed if unknown."""

    return contact_lane_for(identity).provider_family


def share_provider_family(a: ContactIdentity, b: ContactIdentity) -> bool:
    """Return whether ``a`` and ``b`` are the same provider family.

    Raises ``UnknownContactError`` if either identity is not in the table:
    an unknown contact is never independent of anything (fail closed).
    """

    return provider_family_for(a) == provider_family_for(b)
# [[GitHub Copilot CLI | Claude Sonnet 5 | 2026-09-20 | 03:01 UTC | END]]
# [[Claude Code | Claude Fable 5.1 | 2026-09-20 | 03:40 UTC | END]]
