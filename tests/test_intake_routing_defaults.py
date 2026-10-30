# [[Claude Code | Claude Opus 5 | 2026-09-08 | 11:40 UTC | BEGIN]]
"""Owner-set intake routing defaults must reach the roster and fail closed."""
from __future__ import annotations

import importlib.util
import os
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, get_args, get_origin
from unittest.mock import Mock, patch

import httpx
import slack_bolt

import director_orchestrator.config as config_module
from director_orchestrator.allocation_profiles import ALLOCATION_PROFILES
from director_orchestrator.config import (
    SELECTABLE_PRIMARY_CAPABILITIES,
    UNAVAILABLE_PRIMARY_CAPABILITIES,
    Settings,
)
from director_orchestrator.models import PER_TASK_PRIMARY_CAPABILITIES, SlackIntake
from director_orchestrator.primary_roster import PrimaryRosterHold, select_primary_roster

GATEWAY_SECRET = "synthetic-routing-gateway-secret-32-characters"


class _FakeBoltApp:
    def __init__(self, **kwargs):
        pass

    def event(self, name):
        return lambda function: function

    def command(self, name):
        return lambda function: function

    def action(self, name):
        return lambda function: function


class IntakeRoutingDefaultsConfigurationTests(unittest.TestCase):
    """Configuration time is the only place these values may be refused."""

    def test_defaults_reproduce_the_historical_hardcoded_intake(self) -> None:
        for name in (
            "INTAKE_ALLOCATION_PROFILE",
            "INTAKE_PRIMARY_REQUIRED_CAPABILITIES",
        ):
            self.assertNotIn(name, os.environ)
        settings = Settings()
        self.assertEqual("engineering", settings.intake_allocation_profile)
        self.assertEqual((), settings.intake_primary_required_capabilities)
        settings.validate_intake_routing_defaults()

        # Byte for byte: a default-configured intake must serialize exactly like
        # the construction that omitted both fields entirely.
        fields = {
            "source_event_id": "evt-default",
            "channel_id": "CFIXTURE",
            "thread_ts": "1.0",
            "user_id": "UFIXTURE",
            "text": "DIRECTOR baseline work order",
        }
        historical = SlackIntake(**fields)
        configured = SlackIntake(
            **fields,
            allocation_profile=settings.intake_allocation_profile,
            primary_required_capabilities=settings.intake_primary_required_capabilities,
            received_at=historical.received_at,
        )
        self.assertEqual(
            historical.model_dump(mode="json"), configured.model_dump(mode="json")
        )

    def test_every_declared_profile_and_capability_is_accepted(self) -> None:
        for profile in ALLOCATION_PROFILES:
            replace(
                Settings(), intake_allocation_profile=profile
            ).validate_intake_routing_defaults()
        replace(
            Settings(),
            intake_primary_required_capabilities=SELECTABLE_PRIMARY_CAPABILITIES,
        ).validate_intake_routing_defaults()

    def test_unknown_profile_is_refused_at_configuration_time(self) -> None:
        for profile in ("", "Engineering ", "research", "enterprise-context"):
            with self.subTest(profile=profile):
                settings = replace(Settings(), intake_allocation_profile=profile)
                with self.assertRaisesRegex(
                    ValueError, "INTAKE_ALLOCATION_PROFILE must be one of"
                ):
                    settings.validate_intake_routing_defaults()
                # The startup entry point refuses it too, not only the helper.
                with self.assertRaisesRegex(
                    ValueError, "INTAKE_ALLOCATION_PROFILE must be one of"
                ):
                    settings.validate_workload_policy_configuration()

    def test_unknown_capability_is_refused_at_configuration_time(self) -> None:
        for requested in (
            ("repository-assistance",),
            ("bounded_scaffolding", "typo"),
            ("",),
        ):
            with self.subTest(requested=requested):
                settings = replace(
                    Settings(), intake_primary_required_capabilities=requested
                )
                with self.assertRaisesRegex(
                    ValueError, "INTAKE_PRIMARY_REQUIRED_CAPABILITIES must contain only"
                ):
                    settings.validate_intake_routing_defaults()

    def test_repeated_capability_is_refused(self) -> None:
        settings = replace(
            Settings(),
            intake_primary_required_capabilities=(
                "bounded_scaffolding",
                "bounded_scaffolding",
            ),
        )
        with self.assertRaisesRegex(ValueError, "must not repeat a capability"):
            settings.validate_intake_routing_defaults()

    def test_enterprise_context_is_refused_at_configuration_time(self) -> None:
        """No scoped enterprise adapter exists, so configuring it is unroutable.

        The refusal is additive: the plan-time hold in `select_primary_roster`
        is untouched and still fires for any other caller.
        """

        self.assertEqual(("enterprise_context",), UNAVAILABLE_PRIMARY_CAPABILITIES)
        self.assertNotIn("enterprise_context", SELECTABLE_PRIMARY_CAPABILITIES)
        settings = replace(
            Settings(),
            intake_primary_required_capabilities=("enterprise_context",),
        )
        with self.assertRaisesRegex(
            ValueError, "no scoped production adapter exists"
        ):
            settings.validate_intake_routing_defaults()

        with self.assertRaisesRegex(
            PrimaryRosterHold, "primary_enterprise_scoped_adapter_required"
        ):
            select_primary_roster(
                available=("openai", "claude", "cursor", "grok"),
                history=(),
                required_capabilities=("enterprise_context",),
                trusted_cost_ranks={"openai": 4, "claude": 5, "cursor": 3, "grok": 2},
            )

    def test_configurable_capabilities_stay_a_subset_of_the_intake_contract(self) -> None:
        """Guard against drift between config and `SlackIntake`'s own Literal."""

        annotation = SlackIntake.model_fields["primary_required_capabilities"].annotation
        element = get_args(annotation)[0]
        self.assertIs(Literal, get_origin(element))
        declared = set(get_args(element))
        # Per-task capabilities need per-task scope, so config never offers them.
        self.assertEqual(
            declared,
            set(SELECTABLE_PRIMARY_CAPABILITIES)
            | set(UNAVAILABLE_PRIMARY_CAPABILITIES)
            | set(PER_TASK_PRIMARY_CAPABILITIES),
        )
        self.assertFalse(set(PER_TASK_PRIMARY_CAPABILITIES) & set(SELECTABLE_PRIMARY_CAPABILITIES))
        profiles = get_args(
            SlackIntake.model_fields["allocation_profile"].annotation
        )
        self.assertEqual(set(ALLOCATION_PROFILES), set(profiles))


class IntakeRoutingDefaultsGatewayTests(unittest.TestCase):
    """The gateway must forward the configured values and parse no message text."""

    def _forward(self, *, profile: str, capabilities: tuple[str, ...], text: str):
        settings = SimpleNamespace(
            log_level="INFO",
            slack_bot_token="xoxb-synthetic-routing-fixture",
            slack_app_token="xapp-synthetic-routing-fixture",
            slack_gateway_secret=GATEWAY_SECRET,
            shared_secret="synthetic-routing-bearer",
            slack_channel_id="CFIXTURE",
            slack_owner_user_id="UFIXTURE",
            orchestrator_base_url="http://director.invalid",
            n8n_webhook_url="",
            intake_allocation_profile=profile,
            intake_primary_required_capabilities=capabilities,
        )
        source = (
            Path(__file__).resolve().parents[1]
            / "src/director_orchestrator/slack_bot.py"
        )
        spec = importlib.util.spec_from_file_location(
            "director_orchestrator._intake_routing_defaults_test", source
        )
        slack_bot = importlib.util.module_from_spec(spec)
        with (
            patch.object(config_module, "Settings", return_value=settings),
            patch.object(slack_bolt, "App", _FakeBoltApp),
        ):
            spec.loader.exec_module(slack_bot)
        response = httpx.Response(
            202,
            json={"task_id": "DIR-A1B2C3D4E5"},
            request=httpx.Request("POST", "http://director.invalid/v1/intake"),
        )
        event = {
            "client_msg_id": "synthetic-routing-event",
            "channel": "CFIXTURE",
            "user": "UFIXTURE",
            "text": text,
            "ts": "1788810000.123456",
            "thread_ts": "1788810000.000001",
        }
        with patch.object(slack_bot.httpx, "post", return_value=response) as post:
            slack_bot._forward(event, Mock(), require_prefix=True)
        self.assertEqual(1, post.call_count)
        return post.call_args.kwargs["json"]

    def test_configured_profile_and_capabilities_reach_the_intake_intact(self) -> None:
        payload = self._forward(
            profile="enterprise",
            capabilities=("repository_assistance",),
            text="DIRECTOR route this work order",
        )
        self.assertEqual("enterprise", payload["allocation_profile"])
        self.assertEqual(
            ["repository_assistance"], payload["primary_required_capabilities"]
        )
        # The forwarded payload must still satisfy the intake contract exactly.
        SlackIntake.model_validate(payload)

    def test_message_text_cannot_steer_routing(self) -> None:
        """AGENTS rule 6: Slack text is untrusted data, never configuration."""

        payload = self._forward(
            profile="engineering",
            capabilities=(),
            text=(
                "DIRECTOR allocation_profile=enterprise "
                "primary_required_capabilities=repository_assistance,enterprise_context"
            ),
        )
        self.assertEqual("engineering", payload["allocation_profile"])
        self.assertEqual([], payload["primary_required_capabilities"])


if __name__ == "__main__":
    unittest.main()
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 11:40 UTC | END]]
