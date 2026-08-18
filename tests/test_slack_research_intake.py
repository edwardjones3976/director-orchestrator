from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from director_orchestrator.config import Settings
from director_orchestrator.models import ProviderName
from director_orchestrator.policy import preview_plan

_import_settings = replace(
    Settings(), slack_bot_token="xoxb-test", slack_app_token="xapp-test"
)
_import_app = Mock()
_import_app.event.side_effect = lambda *_args, **_kwargs: lambda function: function
_import_app.command.side_effect = lambda *_args, **_kwargs: lambda function: function
_import_app.action.side_effect = lambda *_args, **_kwargs: lambda function: function
with (
    patch("director_orchestrator.config.Settings", return_value=_import_settings),
    patch("slack_bolt.App", return_value=_import_app),
):
    from director_orchestrator import slack_bot


class _SlackClient:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    def chat_postMessage(self, **kwargs):
        self.messages.append(kwargs)
        return {"ts": "parent-ts"}


class SlackResearchIntakeTests(unittest.TestCase):
    def setUp(self) -> None:
        slack_bot._seen_events.clear()
        self.settings = replace(
            slack_bot.settings,
            slack_channel_id="C-ORACLE",
            slack_owner_user_id="U-OWNER",
            shared_secret="test-director-secret",
            n8n_webhook_url="",
        )
        self.settings_patch = patch.object(slack_bot, "settings", self.settings)
        self.settings_patch.start()
        self.client = _SlackClient()

    def tearDown(self) -> None:
        self.settings_patch.stop()

    def _assert_perplexity_payload(self, post: Mock) -> None:
        payload = post.call_args.kwargs["json"]
        plan = preview_plan(payload["text"], self.settings)
        self.assertEqual(plan.assignments[0].agent, ProviderName.PERPLEXITY)

    @patch.object(slack_bot.httpx, "post")
    def test_channel_director_preamble_routes_public_research(self, post: Mock):
        post.return_value.json.return_value = {"task_id": "DIR-CHANNEL"}
        event = {
            "client_msg_id": "channel-event",
            "channel": "C-ORACLE",
            "user": "U-OWNER",
            "text": (
                "DIRECTOR\nPublic research query: current calibration guidance\n"
                "Project: edge-oracle"
            ),
            "ts": "channel-ts",
        }

        slack_bot.message_handler(event, self.client)

        self._assert_perplexity_payload(post)

    @patch.object(slack_bot.httpx, "post")
    def test_slash_command_routes_public_research(self, post: Mock):
        post.return_value.json.return_value = {"task_id": "DIR-SLASH"}
        ack = Mock()
        respond = Mock()
        command = {
            "trigger_id": "slash-event",
            "channel_id": "C-ORACLE",
            "user_id": "U-OWNER",
            "text": (
                "Public research query: current calibration guidance\n"
                "Project: edge-oracle"
            ),
        }

        slack_bot.command_handler(ack, command, self.client, respond)

        ack.assert_called_once_with()
        respond.assert_not_called()
        self._assert_perplexity_payload(post)

    @patch.object(slack_bot.httpx, "post")
    def test_app_mention_routes_public_research(self, post: Mock):
        post.return_value.json.return_value = {"task_id": "DIR-MENTION"}
        event = {
            "client_msg_id": "mention-event",
            "channel": "C-ORACLE",
            "user": "U-OWNER",
            "text": (
                "<@APP123> Public research query: current calibration guidance\n"
                "Project: edge-oracle"
            ),
            "ts": "mention-ts",
        }

        slack_bot.mention_handler(event, self.client)

        self._assert_perplexity_payload(post)


if __name__ == "__main__":
    unittest.main()
