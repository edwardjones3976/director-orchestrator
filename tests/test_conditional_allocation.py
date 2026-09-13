# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 10:40 UTC | BEGIN]]
# [[Gemini Antigravity | Gemini 3.8 Flash (Medium) | 2026-09-13 | 10:30 UTC | BEGIN]]
# [[Gemini Antigravity | Gemini 3.8 Flash (High) | 2026-09-13 | 10:25 UTC | BEGIN]]
"""Unit tests for conditional allocation profile descriptor (Issue 207)."""

import dataclasses
import unittest
from dataclasses import FrozenInstanceError
from types import MappingProxyType

from director_orchestrator.allocation_profiles import (
    ALLOCATION_PROFILES,
    DEFAULT_ALLOCATION_PROFILE,
    LANES,
    allocation_profile_digest,
    allocation_targets,
)
from director_orchestrator.conditional_allocation import (
    CONDITIONAL_CONTACTS,
    CONDITIONAL_EFFECTIVE_DATE_FLOOR,
    CONDITIONAL_HARD_TOLERANCE,
    CONDITIONAL_LANES,
    CONDITIONAL_POLICY_FAMILIES,
    CONDITIONAL_PREREQUISITES,
    CONDITIONAL_PROFILE,
    CONDITIONAL_PROFILE_DIGEST,
    CONDITIONAL_PROFILE_NAME,
    CONDITIONAL_PROFILE_STATUS,
    CONDITIONAL_PROFILE_VERSION,
    CONDITIONAL_SLOT_PERCENTAGE,
    CONDITIONAL_SOFT_TOLERANCE,
    CONDITIONAL_TARGETS,
    CONDITIONAL_WINDOW,
    OPENAI_DIRECT_PRODUCTION_TARGET_PERCENT,
    OPENAI_DIRECTOR_ACTIVITY_FLOOR_PERCENT,
    OPENAI_DIRECTOR_FLOOR_DENOMINATOR,
    OPENAI_PRODUCTION_TARGET_DENOMINATOR,
    RESEARCH_SPECIALTY_BOUNDARIES,
    get_conditional_profile,
    shares_policy_family,
)

VERIFIED_LEGACY_DIGESTS = {
    "engineering": "sha256:1928779a3d24e9172a8f74911b601bc01905dc2dbfd49c306f0dcff73e442a68",
    "enterprise": "sha256:db08376ea3c632828f326d6e08f4ead2a865bd8fbb7faa1396969275c5a8815a",
    "mixed": "sha256:f228e056553673abdffce8f84af45801deb73e0b2bd612930e07fcc36980e976",
    "public_research": "sha256:81687a465f4fcbae938733dc83cddc2ad293e519983f4f9c79394f41a9d9474c",
}


class TestConditionalAllocation(unittest.TestCase):
    """Test suite verifying Issue 207 conditional profile static contract."""

    def test_exact_targets_order_and_equality(self):
        """Verify exact eleven targets, total 100%, and expected equality relationships."""
        expected_order = (
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
        self.assertEqual(CONDITIONAL_LANES, expected_order)
        self.assertEqual(tuple(CONDITIONAL_TARGETS.keys()), expected_order)

        expected_values = (20, 18, 13, 13, 13, 10, 5, 3, 2, 2, 1)
        self.assertEqual(tuple(CONDITIONAL_TARGETS.values()), expected_values)
        self.assertEqual(sum(CONDITIONAL_TARGETS.values()), 100)

        # Equal targets verification
        self.assertEqual(CONDITIONAL_TARGETS["cursor"], 13)
        self.assertEqual(CONDITIONAL_TARGETS["grok"], 13)
        self.assertEqual(CONDITIONAL_TARGETS["grok-bot"], 13)
        self.assertEqual(CONDITIONAL_TARGETS["cursor"], CONDITIONAL_TARGETS["grok"])
        self.assertEqual(CONDITIONAL_TARGETS["grok"], CONDITIONAL_TARGETS["grok-bot"])

        self.assertEqual(CONDITIONAL_TARGETS["github-copilot"], 2)
        self.assertEqual(CONDITIONAL_TARGETS["vscode-coding-bot"], 2)
        self.assertEqual(CONDITIONAL_TARGETS["github-copilot"], CONDITIONAL_TARGETS["vscode-coding-bot"])

        # Specific lane percentages
        self.assertEqual(CONDITIONAL_TARGETS["antigravity"], 20)
        self.assertEqual(CONDITIONAL_TARGETS["zai"], 18)
        self.assertEqual(CONDITIONAL_TARGETS["openai"], 10)
        self.assertEqual(CONDITIONAL_TARGETS["claude"], 5)
        self.assertEqual(CONDITIONAL_TARGETS["perplexity"], 3)
        self.assertEqual(CONDITIONAL_TARGETS["firecrawl"], 1)

    def test_canonical_digest_binds_all_semantic_fields(self):
        """Verify canonical digest derives from and binds all semantic descriptor fields."""
        profile = get_conditional_profile()
        digest = profile.digest

        self.assertTrue(digest.startswith("sha256:"))
        self.assertEqual(len(digest), 71)
        self.assertEqual(digest, CONDITIONAL_PROFILE_DIGEST)

        # Changing prerequisites changes digest
        modified_prereq = dataclasses.replace(profile, prerequisites=())
        self.assertNotEqual(digest, modified_prereq.digest)

        # Changing effective date floor changes digest
        modified_date = dataclasses.replace(profile, effective_date_floor="2026-10-01")
        self.assertNotEqual(digest, modified_date.digest)

        # Changing OpenAI floor percent changes digest
        modified_floor = dataclasses.replace(profile, openai_director_activity_floor_percent=80)
        self.assertNotEqual(digest, modified_floor.digest)

        # Changing status changes digest
        modified_status = dataclasses.replace(profile, status="ACTIVE")
        self.assertNotEqual(digest, modified_status.digest)

    def test_immutability_including_nested_metadata(self):
        """Verify profile descriptor, contacts, prerequisites, and mappings are fully immutable."""
        profile = get_conditional_profile()

        # Mapping immutability
        with self.assertRaises(TypeError):
            profile.targets["antigravity"] = 99  # type: ignore

        with self.assertRaises(TypeError):
            profile.contacts["zai"] = None  # type: ignore

        with self.assertRaises(TypeError):
            profile.policy_families["grok"] = "OpenAI"  # type: ignore

        with self.assertRaises(TypeError):
            CONDITIONAL_TARGETS["openai"] = 50  # type: ignore

        with self.assertRaises(TypeError):
            CONDITIONAL_POLICY_FAMILIES["zai"] = "xAI"  # type: ignore

        # Dataclass field immutability (FrozenInstanceError)
        with self.assertRaises((FrozenInstanceError, AttributeError, TypeError)):
            profile.status = "ACTIVE"  # type: ignore

        with self.assertRaises((FrozenInstanceError, AttributeError, TypeError)):
            profile.is_bound = True  # type: ignore

        contact = profile.get_contact("zai")
        with self.assertRaises((FrozenInstanceError, AttributeError, TypeError)):
            contact.target_percent = 25  # type: ignore

        with self.assertRaises((FrozenInstanceError, AttributeError, TypeError)):
            contact.binding_status = "bound"  # type: ignore

        prereq = profile.prerequisites[0]
        with self.assertRaises((FrozenInstanceError, AttributeError, TypeError)):
            prereq.requirement = "modified"  # type: ignore

    def test_digest_binds_contact_lookup_keys(self):
        profile = get_conditional_profile()
        renamed_contacts = MappingProxyType({
            "unknown-contact" if key == "grok" else key: contact
            for key, contact in profile.contacts.items()
        })
        reconstructed = dataclasses.replace(profile, contacts=renamed_contacts)
        with self.assertRaises(KeyError):
            profile.get_contact("unknown-contact")
        self.assertEqual(
            reconstructed.get_contact("unknown-contact"), profile.get_contact("grok")
        )
        self.assertNotEqual(reconstructed.digest, profile.digest)

    def test_policy_families_and_contact_separation(self):
        """Verify policy families group expected lanes, contacts stay distinct, and unknown contacts fail closed."""
        profile = get_conditional_profile()

        # xAI grouping
        self.assertEqual(profile.policy_family_for("grok"), "xAI")
        self.assertEqual(profile.policy_family_for("grok-bot"), "xAI")
        self.assertTrue(profile.shares_policy_family("grok", "grok-bot"))
        self.assertTrue(shares_policy_family("grok", "grok-bot"))

        # Contacts remain distinct despite shared family
        contact_grok = profile.get_contact("grok")
        contact_bot = profile.get_contact("grok-bot")
        self.assertNotEqual(contact_grok.lane_id, contact_bot.lane_id)
        self.assertNotEqual(contact_grok.product_name, contact_bot.product_name)
        self.assertEqual(contact_grok.product_name, "Direct Grok")
        self.assertEqual(contact_bot.product_name, "Grok Bot")

        # GitHub/Microsoft grouping
        self.assertEqual(profile.policy_family_for("github-copilot"), "GitHub/Microsoft")
        self.assertEqual(profile.policy_family_for("vscode-coding-bot"), "GitHub/Microsoft")
        self.assertTrue(profile.shares_policy_family("github-copilot", "vscode-coding-bot"))

        contact_copilot = profile.get_contact("github-copilot")
        contact_vscode = profile.get_contact("vscode-coding-bot")
        self.assertNotEqual(contact_copilot.lane_id, contact_vscode.lane_id)
        self.assertNotEqual(contact_copilot.product_name, contact_vscode.product_name)

        # Different families do not share
        self.assertFalse(profile.shares_policy_family("cursor", "grok"))
        self.assertFalse(profile.shares_policy_family("openai", "claude"))

        # Unknown contacts reject with KeyError (never silently return or imply independence)
        with self.assertRaises(KeyError):
            profile.policy_family_for("unregistered-author")

        with self.assertRaises(KeyError):
            profile.shares_policy_family("unregistered-author", "unregistered-reviewer")

        with self.assertRaises(KeyError):
            shares_policy_family("openai", "unregistered-reviewer")

        # No authority/eligibility/admission functions exist on descriptor or contacts
        self.assertFalse(hasattr(profile, "are_independent_reviewers"))
        self.assertFalse(hasattr(profile, "is_slot_admissible"))
        self.assertFalse(hasattr(contact_grok, "is_slot_admissible"))
        self.assertFalse(hasattr(contact_grok, "is_within_soft_band"))
        self.assertFalse(hasattr(contact_grok, "is_within_hard_ceiling"))

    def test_unresolved_vscode_coding_bot(self):
        """Verify VS Code Coding Bot is represented as unresolved with no invented route."""
        profile = get_conditional_profile()
        vscode = profile.get_contact("vscode-coding-bot")

        self.assertFalse(vscode.is_resolved)
        self.assertEqual(vscode.binding_status, "unresolved")
        self.assertIsNone(vscode.route_binding)
        self.assertIn("not identified", vscode.boundary_note or "")
        self.assertEqual(profile.unresolved_lanes(), ("vscode-coding-bot",))

        # Other contacts are resolved but unbound
        for lane in CONDITIONAL_LANES:
            c = profile.get_contact(lane)
            self.assertIsNone(c.route_binding)
            if lane != "vscode-coding-bot":
                self.assertTrue(c.is_resolved)
                self.assertEqual(c.binding_status, "unbound")

    def test_proposed_status_and_named_prerequisites(self):
        """Verify profile remains proposed, unbound, and carries named immutable requirements."""
        profile = get_conditional_profile()

        self.assertEqual(profile.status, "PROPOSED")
        self.assertEqual(profile.name, CONDITIONAL_PROFILE_NAME)
        self.assertEqual(profile.version, CONDITIONAL_PROFILE_VERSION)
        self.assertEqual(profile.effective_date_floor, CONDITIONAL_EFFECTIVE_DATE_FLOOR)
        self.assertFalse(profile.is_bound)
        self.assertFalse(profile.confers_runtime_readiness)
        self.assertFalse(profile.confers_enrollment)

        # Prerequisites are named requirements, not boolean observations
        self.assertEqual(len(profile.prerequisites), 6)
        for prereq in profile.prerequisites:
            self.assertIsInstance(prereq.name, str)
            self.assertIsInstance(prereq.requirement, str)
            self.assertFalse(hasattr(prereq, "satisfied"))

        prereq_names = {p.name for p in profile.prerequisites}
        self.assertIn("effective_date_boundary", prereq_names)
        self.assertIn("owner_zai_qualification_acceptance", prereq_names)
        self.assertIn("owner_activation_and_subscription_approval", prereq_names)
        self.assertIn("deterministic_safety_and_ci_gates", prereq_names)
        self.assertIn("dual_activity_ledgers_operational", prereq_names)
        self.assertIn("vscode_coding_bot_integration_resolved", prereq_names)

    def test_pinned_legacy_profiles_and_digests_unchanged(self):
        """Verify legacy allocation profiles, lanes, default, and exact pinned digests remain untouched."""
        self.assertEqual(
            LANES,
            ("openai", "claude", "cursor", "grok", "antigravity", "perplexity", "firecrawl", "github-copilot"),
        )
        self.assertEqual(DEFAULT_ALLOCATION_PROFILE, "engineering")
        self.assertEqual(
            set(ALLOCATION_PROFILES.keys()),
            {"engineering", "mixed", "public_research", "enterprise"},
        )
        self.assertEqual(
            tuple(allocation_targets("engineering").values()),
            (40, 25, 15, 10, 10, 0, 0, 0),
        )
        self.assertEqual(
            tuple(allocation_targets("mixed").values()),
            (27, 33, 10, 10, 5, 5, 5, 5),
        )
        self.assertEqual(
            tuple(allocation_targets("public_research").values()),
            (20, 25, 5, 5, 5, 25, 15, 0),
        )
        self.assertEqual(
            tuple(allocation_targets("enterprise").values()),
            (32, 38, 5, 5, 5, 0, 5, 10),
        )

        # Pin the exact verified legacy digests
        for profile_name, expected_digest in VERIFIED_LEGACY_DIGESTS.items():
            self.assertEqual(allocation_profile_digest(profile_name), expected_digest)

    def test_accurate_soft_and_hard_tolerance_metadata(self):
        """Verify window, tolerance bands, and Firecrawl mathematical boundary without admission."""
        profile = get_conditional_profile()

        self.assertEqual(profile.window, 20)
        self.assertEqual(profile.slot_percentage, 5)
        self.assertEqual(profile.soft_tolerance_percentage_points, 3)
        self.assertEqual(profile.hard_tolerance_percentage_points, 5)

        for lane, contact in profile.contacts.items():
            target = contact.target_percent
            expected_lower = max(0, target - 3)
            expected_upper = target + 3
            expected_hard = target + 5
            self.assertEqual(contact.soft_tolerance_band, (expected_lower, expected_upper))
            self.assertEqual(contact.hard_ceiling, expected_hard)

        # Firecrawl specific metadata check
        fc = profile.get_contact("firecrawl")
        self.assertEqual(fc.target_percent, 1)
        self.assertEqual(fc.soft_tolerance_band, (0, 4))
        self.assertEqual(fc.hard_ceiling, 6)
        # Mathematical slot relation: 5% slot is > 4% (outside normal band) but <= 6% (within hard ceiling)
        self.assertGreater(5, fc.soft_tolerance_band[1])
        self.assertLessEqual(5, fc.hard_ceiling)

    def test_openai_director_floor_and_specialty_boundaries(self):
        """Verify OpenAI 75% floor constants, distinct denominators, and research specialties."""
        self.assertEqual(OPENAI_DIRECTOR_ACTIVITY_FLOOR_PERCENT, 75)
        self.assertEqual(OPENAI_DIRECT_PRODUCTION_TARGET_PERCENT, 10)
        self.assertNotEqual(OPENAI_DIRECTOR_FLOOR_DENOMINATOR, OPENAI_PRODUCTION_TARGET_DENOMINATOR)
        self.assertEqual(OPENAI_DIRECTOR_FLOOR_DENOMINATOR, "authenticated_openai_activity")
        self.assertEqual(OPENAI_PRODUCTION_TARGET_DENOMINATOR, "global_accepted_production")

        # Specialty boundaries
        self.assertEqual(
            RESEARCH_SPECIALTY_BOUNDARIES,
            {
                "perplexity": "public_research_evidence_only",
                "firecrawl": "authorized_public_retrieval_only",
            },
        )


if __name__ == "__main__":
    unittest.main()
# [[Gemini Antigravity | Gemini 3.8 Flash (High) | 2026-09-13 | 10:25 UTC | END]]
# [[Gemini Antigravity | Gemini 3.8 Flash (Medium) | 2026-09-13 | 10:30 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 10:40 UTC | END]]
