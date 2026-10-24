"""Grok Bot desktop MCP gates; no GUI input is made by these tests."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import grok_bot_mcp as bot


class GrokBotMcpTests(unittest.TestCase):
    def test_missing_or_modified_dispatcher_refuses(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "grok_bot_chat.ps1"
            with patch.object(bot, "SCRIPT", path):
                self.assertFalse(bot.script_ready()[0])
                path.write_text("unreviewed", encoding="ascii")
                self.assertFalse(bot.script_ready()[0])

    def test_windows_line_endings_match_reviewed_git_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "grok_bot_chat.ps1"
            path.write_bytes(b"first\r\nsecond\r\n")
            expected = hashlib.sha256(b"first\nsecond\n").hexdigest()
            with patch.object(bot, "SCRIPT", path), patch.object(bot, "SCRIPT_SHA256", expected):
                self.assertEqual(bot.script_ready(), (True, "ready"))

    def test_desktop_input_and_send_are_separate(self):
        with patch.dict(bot.os.environ, {"GROK_BOT_DESKTOP_INPUT_ENABLED": "",
                                         "GROK_BOT_DESKTOP_SEND_ENABLED": ""}):
            with self.assertRaisesRegex(ValueError, "input is disabled"):
                bot.paste("unused")
            with self.assertRaisesRegex(ValueError, "send is disabled"):
                bot.send("a" * 64, "b" * 64)

    def test_preview_hash_must_match_before_dispatch(self):
        with tempfile.TemporaryDirectory() as temp:
            digest = "a" * 64
            path = Path(temp) / f"{digest}.preview.png"
            path.write_bytes(b"synthetic preview")
            with patch.object(bot, "ARTIFACT_ROOT", Path(temp)), \
                    patch.dict(bot.os.environ, {"GROK_BOT_DESKTOP_SEND_ENABLED": "true"}), \
                    patch.object(bot, "dispatch") as dispatch:
                self.assertEqual(bot.preview(digest)["preview_sha256"],
                                 hashlib.sha256(b"synthetic preview").hexdigest())
                with self.assertRaisesRegex(ValueError, "preview changed"):
                    bot.send(digest, "b" * 64)
                dispatch.assert_not_called()

    def test_packet_validation_stops_before_gui(self):
        with tempfile.TemporaryDirectory() as temp:
            packet = Path(temp) / "packet.txt"
            packet.write_text("hello", encoding="ascii")
            with patch.object(bot, "ARTIFACT_ROOT", Path(temp)), \
                    patch.dict(bot.os.environ, {"GROK_BOT_DESKTOP_INPUT_ENABLED": "true"}), \
                    patch.object(bot, "dispatch") as dispatch:
                with self.assertRaisesRegex(ValueError, "VERDICT"):
                    bot.paste(str(packet))
                dispatch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
