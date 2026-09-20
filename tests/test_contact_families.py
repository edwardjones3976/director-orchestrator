# [[Claude Code | Claude Fable 5.1 | 2026-09-20 | 03:40 UTC | BEGIN]]
# [[GitHub Copilot CLI | Claude Sonnet 5 | 2026-09-20 | 03:01 UTC | BEGIN]]
"""Tests for typed contact-lane and provider-family identities (directive #207)."""

from __future__ import annotations

import importlib
import unittest

from director_orchestrator import zai_lane
from director_orchestrator.contact_families import (
    CONTACT_LANES_BY_ID,
    ContactLane,
    UnknownContactError,
    contact_lane_for,
    provider_family_for,
    share_provider_family,
)
from director_orchestrator.models import ProviderName


class ContactFamilyTableTests(unittest.TestCase):
    def test_every_known_contact_is_typed_and_has_a_family(self):
        expected = {
            "openai": "OpenAI",
            "claude": "Anthropic",
            "cursor": "Anysphere",
            "grok": "xAI",
            "grok-bot": "xAI",
            "gemini": "Google",
            "antigravity": "Google",
            "perplexity": "Perplexity",
            "firecrawl": "Firecrawl",
            "github-copilot": "GitHub/Microsoft",
            "vscode-coding-bot": "GitHub/Microsoft",
            "zai": "Z.ai",
            "owner": "Owner",
        }
        self.assertEqual(set(CONTACT_LANES_BY_ID), set(expected))
        for lane_id, family in expected.items():
            with self.subTest(lane_id=lane_id):
                lane = CONTACT_LANES_BY_ID[lane_id]
                self.assertIsInstance(lane, ContactLane)
                self.assertEqual(lane.lane_id, lane_id)
                self.assertEqual(lane.provider_family, family)
                self.assertEqual(provider_family_for(lane_id), family)

    def test_contact_lane_for_returns_the_typed_lane(self):
        lane = contact_lane_for("github-copilot")
        self.assertEqual(lane.product, "GitHub Copilot CLI")
        self.assertEqual(lane.provider_family, "GitHub/Microsoft")

    def test_accepts_provider_name_enum_members_as_well_as_strings(self):
        for member, family in (
            (ProviderName.CLAUDE, "Anthropic"),
            (ProviderName.CURSOR, "Anysphere"),
            (ProviderName.GEMINI, "Google"),
            (ProviderName.GROK, "xAI"),
            (ProviderName.PERPLEXITY, "Perplexity"),
            (ProviderName.OPENAI, "OpenAI"),
            (ProviderName.FIRECRAWL, "Firecrawl"),
            (ProviderName.GITHUB_COPILOT, "GitHub/Microsoft"),
        ):
            with self.subTest(member=member):
                self.assertEqual(provider_family_for(member), family)
                self.assertEqual(provider_family_for(member), provider_family_for(member.value))


class ShareProviderFamilyTests(unittest.TestCase):
    def test_same_family_is_rejected_as_not_independent_grok_and_grok_bot(self):
        # Direct Grok and Grok Bot are one xAI family: never false independence.
        self.assertTrue(share_provider_family("grok", "grok-bot"))
        self.assertTrue(share_provider_family("grok-bot", "grok"))

    def test_same_family_is_rejected_as_not_independent_copilot_and_vscode_bot(self):
        # GitHub Copilot and VS Code Coding Bot are one GitHub/Microsoft family.
        self.assertTrue(share_provider_family("github-copilot", "vscode-coding-bot"))

    def test_gemini_and_antigravity_share_the_google_family(self):
        self.assertTrue(share_provider_family("gemini", "antigravity"))

    def test_different_families_are_accepted_as_independent(self):
        for a, b in (
            ("claude", "cursor"),
            ("claude", "grok"),
            ("openai", "github-copilot"),
            ("perplexity", "firecrawl"),
            ("zai", "claude"),
        ):
            with self.subTest(a=a, b=b):
                self.assertFalse(share_provider_family(a, b))

    def test_unknown_identity_fails_closed_never_independent(self):
        with self.assertRaises(UnknownContactError):
            provider_family_for("unregistered-reviewer")
        with self.assertRaises(UnknownContactError):
            provider_family_for("some-unregistered-contact")
        with self.assertRaises(UnknownContactError):
            share_provider_family("claude", "unregistered-reviewer")
        with self.assertRaises(UnknownContactError):
            share_provider_family("unregistered-reviewer", "claude")

    def test_unknown_contact_error_is_a_value_error(self):
        # Callers that catch ValueError (the review-handoff and policy
        # consumers) still fail closed on an unknown contact.
        self.assertTrue(issubclass(UnknownContactError, ValueError))


class ZaiLaneConsistencyTests(unittest.TestCase):
    def test_zai_lane_id_matches_the_contact_family_table(self):
        self.assertEqual(zai_lane.ZAI_CONTACT_LANE.lane_id, "zai")
        self.assertEqual(provider_family_for(zai_lane.ZAI_CONTACT_LANE.lane_id), "Z.ai")


class ConditionalAllocationConsistencyTests(unittest.TestCase):
    def test_conditional_policy_families_match_this_table_when_present(self):
        """PR #221 may or may not be merged when this runs. If
        ``director_orchestrator.conditional_allocation`` is importable, its
        ``CONDITIONAL_POLICY_FAMILIES`` must equal this table restricted to
        its own lanes. Otherwise this test is skipped, not failed."""

        try:
            conditional_allocation = importlib.import_module(
                "director_orchestrator.conditional_allocation"
            )
        except ImportError:
            self.skipTest(
                "director_orchestrator.conditional_allocation is not present in "
                "this worktree (PR #221 may not be merged yet)"
            )
        families = getattr(conditional_allocation, "CONDITIONAL_POLICY_FAMILIES", None)
        if families is None:
            self.skipTest(
                "director_orchestrator.conditional_allocation has no "
                "CONDITIONAL_POLICY_FAMILIES to compare"
            )
        expected = {
            lane_id: lane.provider_family
            for lane_id, lane in CONTACT_LANES_BY_ID.items()
            if lane_id in families
        }
        self.assertEqual(dict(families), expected)



class ProviderNameCoverageTests(unittest.TestCase):
    """Every ProviderName member resolves, so no existing plan can fail closed as family-unknown."""

    def test_every_provider_name_member_has_a_family(self):
        from director_orchestrator.models import ProviderName

        for member in ProviderName:
            with self.subTest(member=member.value):
                self.assertTrue(provider_family_for(member))
        self.assertEqual(provider_family_for("owner"), "Owner")
        for other in ("openai", "claude", "grok", "github-copilot"):
            self.assertFalse(share_provider_family("owner", other))


if __name__ == "__main__":
    unittest.main()
# [[GitHub Copilot CLI | Claude Sonnet 5 | 2026-09-20 | 03:01 UTC | END]]
# [[Claude Code | Claude Fable 5.1 | 2026-09-20 | 03:40 UTC | END]]
