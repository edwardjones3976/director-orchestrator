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

    def test_author_packet_is_pasted_and_a_bare_line_is_not(self):
        base = "0123456789abcdef0123456789abcdef01234567"
        with tempfile.TemporaryDirectory() as temp:
            packet = Path(temp) / "author.txt"
            with patch.object(bot, "ARTIFACT_ROOT", Path(temp)), \
                    patch.dict(bot.os.environ, {"GROK_BOT_DESKTOP_INPUT_ENABLED": "true"}), \
                    patch.object(bot, "dispatch", return_value={"action": "paste"}) as dispatch:
                packet.write_text(f"task\nAUTHOR: PATCH bound to {base}\nmore\n", encoding="ascii")
                bot.paste(str(packet))
                dispatch.assert_called_once_with("paste", Packet=str(packet.resolve()))
                dispatch.reset_mock()
                packet.write_text(f"task AUTHOR: PATCH bound to {base}\n", encoding="ascii")
                with self.assertRaisesRegex(ValueError, "AUTHOR line"):
                    bot.paste(str(packet))
                packet.write_text(f"AUTHOR: PATCH bound to {base[:12]}\n", encoding="ascii")
                with self.assertRaisesRegex(ValueError, "AUTHOR line"):
                    bot.paste(str(packet))
                dispatch.assert_not_called()

    def test_read_reply_needs_a_sent_packet_and_passes_the_copy_flag(self):
        digest = "c" * 64
        with tempfile.TemporaryDirectory() as temp, patch.object(bot, "ARTIFACT_ROOT", Path(temp)), \
                patch.object(bot, "dispatch", return_value={"action": "read"}) as dispatch:
            with self.assertRaisesRegex(ValueError, "never sent"):
                bot.read_reply(digest, False)
            with self.assertRaisesRegex(ValueError, "lowercase SHA-256"):
                bot.read_reply("C" * 64, False)
            (Path(temp) / f"{digest}.sent.json").write_text("{}", encoding="ascii")
            with self.assertRaisesRegex(ValueError, "true or false"):
                bot.read_reply(digest, "yes")
            dispatch.assert_not_called()
            bot.read_reply(digest, False)
            dispatch.assert_called_with("read", flags=(), Digest=digest)
            bot.call("grok_bot_read_reply", {"digest": digest, "copy_code": True})
            dispatch.assert_called_with("read", flags=("CopyCode",), Digest=digest)

    def test_flags_reach_the_dispatcher_command_line(self):
        with patch.object(bot, "script_ready", return_value=(True, "ready")), \
                patch.object(bot.Path, "is_file", return_value=True), \
                patch.object(bot.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "ok"
            bot.dispatch("read", flags=("CopyCode",), Digest="d" * 64)
            command = run.call_args[0][0]
            self.assertEqual(command[-3:], ["-Digest", "d" * 64, "-CopyCode"])

    def test_pinned_digest_is_the_checked_in_dispatcher(self):
        raw = (Path(bot.__file__).resolve().parent / "grok-bot-chat" / "grok_bot_chat.ps1").read_bytes()
        self.assertEqual(hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest(), bot.SCRIPT_SHA256)


if __name__ == "__main__":
    unittest.main()
