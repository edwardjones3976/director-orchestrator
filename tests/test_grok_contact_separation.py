"""Grok Bot identity does not inherit Grok routing or readiness evidence."""

import unittest

from director_orchestrator import provider_profiles
from director_orchestrator.contact_families import (
    contact_lane_for,
    share_provider_family,
)


class GrokContactSeparationTests(unittest.TestCase):
    def test_desktop_contact_has_its_own_endpoint_without_runtime_enrollment(self):
        catalog = provider_profiles.load_route_catalog()
        route = next(item for item in catalog["routes"] if item["route_id"] == "grok-bot")
        self.assertEqual(route["endpoints"], {"from": "director", "to": "grok-bot"})
        self.assertEqual(route["providers_involved"], [])
        self.assertEqual(route["status"], "proposed")
        self.assertEqual(route["default_config_state"], "blocked")
        self.assertEqual(route["config"], [])
        self.assertNotIn("grok-bot", catalog["lanes"])
        self.assertIn("grok-bot", {item["id"] for item in catalog["endpoints"]})
        profile = provider_profiles.load_profiles()["grok"]
        self.assertIn("Grok and Grok Bot are separate entities", profile["identifiers"]["alias_note"])
        self.assertNotEqual(contact_lane_for("grok"), contact_lane_for("grok-bot"))
        self.assertTrue(share_provider_family("grok", "grok-bot"))


if __name__ == "__main__":
    unittest.main()
