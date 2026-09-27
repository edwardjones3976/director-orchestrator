# [[OpenAI Codex | GPT-6 | 2026-09-27 | 01:36 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:05 UTC | BEGIN]]
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "site_board", Path(__file__).resolve().parents[1] / "scripts/site-board.py"
)
board = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(board)


class SiteBoardTests(unittest.TestCase):
    def setUp(self):
        self.prs = [
            dict(number=number, title=f"Work {number}", isDraft=number == 1,
                 headRefName=f"work-{number}", headRefOid="a" * 40,
                 body="Session: desk-one\n" if number == 1 else "")
            for number in (1, 2)
        ]
        self.comments = []
        self.calls = []
        self.file_count = 1
        self.files = [{"path": "src/shared.py", "changeType": "MODIFIED"}]
        self.rest_files = []
        self.rest_head = "a" * 40

    def gh(self, command, **kwargs):
        self.calls.append((command, kwargs))
        args = command[1:]
        if args[:2] == ["pr", "list"]:
            result = self.prs
        elif args[:2] == ["pr", "view"]:
            result = dict(files=self.files, changedFiles=self.file_count,
                          headRefOid="a" * 40, state="OPEN")
        elif args == ["api", "user"]:
            result = {"login": "board-writer"}
        elif "/files?" in args[-1]:
            result = self.rest_files
        elif "/pulls/" in args[-1]:
            result = {"state": "open", "head": {"sha": self.rest_head}}
        elif args[:3] in (["api", "-X", "PATCH"], ["api", "-X", "POST"]):
            result = {"id": 9}
        elif "/comments?" in args[-1]:
            result = self.comments if "page=1" in args[-1] else []
        elif "status=queued" in args[-1]:
            result = {"workflow_runs": [
                {"id": 8, "created_at": "2026-09-26T21:00:00Z"},
                {"id": 7, "created_at": "2026-09-26T20:00:00Z"},
            ]}
        elif "head_sha=" in args[-1]:
            result = {"workflow_runs": [{"id": 8, "status": "queued", "conclusion": None}]}
        else:
            self.fail(f"Unexpected command: {command}")
        return subprocess.CompletedProcess(command, 0, json.dumps(result), "")

    def run_board(self, *args):
        output = io.StringIO()
        with patch.object(board.subprocess, "run", side_effect=self.gh), contextlib.redirect_stdout(output):
            code = board.main(list(args))
        return code, output.getvalue()

    def test_board_overlap_session_ci_queue_and_cached_head(self):
        code, output = self.run_board()
        self.assertEqual(code, 0)
        self.assertIn("## OVERLAPS\n\n- src/shared.py: #1, #2", output)
        self.assertIn("draft: true", output)
        self.assertIn("Session: desk-one", output)
        self.assertIn("Session: unknown", output)
        self.assertIn("queued / none; queue position: 2", output)
        self.assertEqual(len(self.calls), 5)  # list, two views, queue, one shared head

    def test_check_exit_codes_and_no_ci_reads(self):
        code, output = self.run_board("--check", "./src/shared.py", "clear.py")
        self.assertEqual(code, 1)
        self.assertIn("#1, #2", output)
        self.assertIn("clear.py: clear", output)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.run_board("--check", "clear.py")[0], 0)
        self.assertEqual(self.run_board("--check", "src\\shared.py")[0], 1)
        self.assertEqual(self.run_board("--check", "src/./shared.py")[0], 1)

    def test_post_updates_marker_comment_using_stdin(self):
        self.comments = [{"id": 5, "body": "unrelated"},
                         {"id": 9, "body": board.MARKER + "\nold", "user": {"login": "board-writer"}},
                         {"id": 10, "body": board.MARKER, "user": {"login": "foreign-writer"}}]
        self.assertEqual(self.run_board("--post", "299")[0], 0)
        command, kwargs = self.calls[-1]
        self.assertEqual(command, ["gh", "api", "-X", "PATCH", f"{board.API}/issues/comments/9", "--input", "-"])
        self.assertTrue(json.loads(kwargs["input"])["body"].startswith(board.MARKER + "\n# Work board"))

    def test_post_creates_when_missing(self):
        self.assertEqual(self.run_board("--post", "299")[0], 0)
        self.assertEqual(self.calls[-1][0][1:5], ["api", "-X", "POST", f"{board.API}/issues/299/comments"])

    def test_duplicate_markers_and_incomplete_files_fail_without_write(self):
        self.comments = [{"id": n, "body": board.MARKER, "user": {"login": "board-writer"}} for n in (1, 2)]
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.run_board("--post", "299")[0], 2)
            self.file_count = 2
            self.assertEqual(self.run_board("--check", "clear.py")[0], 2)
        self.assertFalse(any("-X" in command for command, _ in self.calls))

    def test_post_neutralizes_mentions_in_all_untrusted_fields(self):
        self.prs[0].update(title="`@title-user`", headRefName="@org/team",
                           body="Session: @session-user")
        self.files = [{"path": "src/@file-user.py", "changeType": "MODIFIED"}]
        self.assertEqual(self.run_board("--post", "299")[0], 0)
        body = json.loads(self.calls[-1][1]["input"])["body"]
        for mention in ("title-user", "org/team", "session-user", "file-user"):
            self.assertNotIn("@" + mention, body)
            self.assertIn("@\u200d" + mention, body)
        self.assertIn("\\`@\u200dtitle-user\\`", body)

    def test_inline_encodes_all_terminal_and_bidi_controls(self):
        for codepoint in (*range(0x20), *range(0x7f, 0xa0),
                          *range(0x202a, 0x202f), *range(0x2066, 0x206a)):
            with self.subTest(codepoint=codepoint):
                self.assertEqual(board.inline(chr(codepoint)), "\ufffd")
        self.assertEqual(board.inline("  plain   `@name`  "), "plain \\`@\u200dname\\`")

    def test_board_encodes_terminal_and_bidi_controls_in_remote_fields(self):
        remote = "safe\x1b\x07\x08\x85\u202eend"
        self.prs[0].update(title=remote, headRefName=remote,
                           body="Session: " + remote)
        self.files = [{"path": remote, "changeType": "MODIFIED"}]
        code, output = self.run_board()
        self.assertEqual(code, 0)
        self.assertEqual(output.count("safe" + "\ufffd" * 5 + "end"), 6)
        for control in "\x1b\x07\x08\x85\u202e":
            self.assertNotIn(control, output)

    def test_check_indexes_rename_source_and_destination_with_bounded_calls(self):
        self.files = [{"path": "new.py", "changeType": "RENAMED"}]
        self.rest_files = [{"filename": "new.py", "status": "renamed", "previous_filename": "old.py"}]
        code, output = self.run_board("--check", "old.py", "new.py")
        self.assertEqual(code, 1)
        self.assertIn("old.py: #1, #2", output)
        self.assertIn("new.py: #1, #2", output)
        self.assertEqual(len(self.calls), 7)  # list, two views, two file pages, two head checks

    def test_rename_incomplete_metadata_or_changed_head_fails_closed(self):
        self.files = [{"path": "new.py", "changeType": "RENAMED"}]
        for files, head in (([], "a" * 40),
                            ([{"filename": "new.py", "status": "renamed"}], "a" * 40),
                            ([{"filename": "new.py", "status": "renamed", "previous_filename": "old.py"}], "b" * 40)):
            with self.subTest(files=files, head=head), contextlib.redirect_stderr(io.StringIO()):
                self.rest_files, self.rest_head = files, head
                self.assertEqual(self.run_board("--check", "old.py")[0], 2)

    def test_foreign_markers_do_not_block_creation(self):
        self.comments = [{"id": n, "body": board.MARKER, "user": {"login": "foreign-writer"}} for n in (1, 2)]
        self.comments.append({"id": 3, "body": board.MARKER, "user": None})
        self.assertEqual(self.run_board("--post", "299")[0], 0)
        self.assertEqual(self.calls[-1][0][1:5], ["api", "-X", "POST", f"{board.API}/issues/299/comments"])

    def test_post_identity_read_is_cached(self):
        gh = board.GitHub()
        with patch.object(board.subprocess, "run", side_effect=self.gh):
            board.post_board(gh, 299, "first")
            board.post_board(gh, 299, "second")
        self.assertEqual(sum(command == ["gh", "api", "user"] for command, _ in self.calls), 1)

    def test_error_is_not_a_clear_and_does_not_leak_stderr(self):
        stderr = io.StringIO()
        with patch.object(board.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "gh", stderr="private")), contextlib.redirect_stderr(stderr):
            self.assertEqual(board.main(["--check", "clear.py"]), 2)
        self.assertNotIn("private", stderr.getvalue())

    def test_pagination_bound_fails_and_reads_are_cached(self):
        gh = board.GitHub()
        with patch.object(board.subprocess, "run", return_value=subprocess.CompletedProcess("gh", 0, json.dumps([{}] * 100))) as run:
            with self.assertRaises(board.BoardError):
                gh.pages("example/comments")
            self.assertEqual(run.call_count, board.MAX_PAGES)
            gh.call("api", "example/comments?per_page=100&page=1")
            self.assertEqual(run.call_count, board.MAX_PAGES)


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:05 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 01:36 UTC | END]]
