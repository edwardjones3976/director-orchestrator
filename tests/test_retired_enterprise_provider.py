# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:53 UTC | BEGIN]]
"""Retired enterprise credentials cannot resurrect a removed provider lane."""
import json
import unittest
from dataclasses import replace
from tempfile import TemporaryDirectory
from unittest.mock import patch

from director_orchestrator.config import Settings
from director_orchestrator.models import ProviderName
from director_orchestrator.workload_policy import Provider, DirectProductionProvider
from director_orchestrator.primary_parallel import provider_transport_kind, ParallelDispatchError
from director_orchestrator.primary_transports import (
    PRIMARY_PROVIDER_LANES, PrimaryProviderCapability,
    discover_local_primary_command_transports,
)


class RetiredEnterpriseProviderTests(unittest.TestCase):
    def test_removed_provider_is_rejected_but_github_copilot_remains(self):
        retired = "microsoft-365-copilot"
        for enum in (ProviderName, Provider, DirectProductionProvider):
            with self.subTest(enum=enum), self.assertRaises(ValueError):
                enum(retired)
            self.assertEqual(enum("github-copilot").value, "github-copilot")
        with self.assertRaises(ValueError):
            PrimaryProviderCapability(retired, retired, "api", True, True).validate()
        PrimaryProviderCapability("github-copilot", "github-copilot", "cli", True, True).validate()
        with self.assertRaises(ParallelDispatchError):
            provider_transport_kind("work-iq-rest-production")

    def test_retired_activation_and_cost_rank_configuration_fail_closed(self):
        settings = replace(Settings(),
            langgraph_primary_provider_activation_bindings_json=json.dumps({
                "microsoft-365-copilot": {}}),
            trusted_provider_cost_ranks_json='{"microsoft-365-copilot":1}')
        with self.assertRaisesRegex(ValueError, "unsupported provider"):
            settings.primary_provider_activation_bindings()
        with self.assertRaisesRegex(ValueError, "unsupported provider"):
            settings.trusted_provider_cost_ranks()

    def test_stale_credentials_do_not_discover_a_retired_transport(self):
        with TemporaryDirectory() as root, patch(
            "director_orchestrator.primary_transports._which", return_value=None
        ), patch.dict("os.environ", {
            "MICROSOFT_365_COPILOT_ACCESS_TOKEN": "retired-fixture",
            "MICROSOFT_365_COPILOT_DELEGATED_TOKEN_FILE": "must-not-be-opened",
        }):
            transports, capabilities = discover_local_primary_command_transports(
                cwd=root, authenticated={"microsoft-365-copilot": True})
        self.assertNotIn("microsoft-365-copilot", transports)
        self.assertNotIn("microsoft-365-copilot", capabilities)
        self.assertEqual(set(capabilities), set(PRIMARY_PROVIDER_LANES))
        self.assertIn("github-copilot", capabilities)
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:53 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | END]]
