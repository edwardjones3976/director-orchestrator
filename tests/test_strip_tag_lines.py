import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("strip_tag_lines", ROOT / "scripts/strip-tag-lines.py")
STRIP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STRIP)
TAG = "[[Codex | GPT-6 | 2026-09-26 | 22:05 UTC | BEGIN]]"


class StripTagLinesTests(unittest.TestCase):
    def test_comment_forms_bold_bom_line_endings_and_idempotence(self):
        for start, end in STRIP.WRAPPERS:
            for newline in (b"\n", b"\r\n", b"\r"):
                with self.subTest(wrapper=(start, end), newline=newline):
                    original = b"\xef\xbb\xbf" + (start + TAG + end).encode() + newline
                    original += b"keep" + newline + (start + TAG.replace("BEGIN", "END") + end).encode()
                    expected = b"\xef\xbb\xbfkeep" + newline
                    self.assertEqual(STRIP.strip_lines(original), expected)
                    self.assertEqual(STRIP.strip_lines(expected), expected)
        self.assertEqual(STRIP.strip_lines(b"first\r\nsecond\nlast"), b"first\r\nsecond\nlast")

    def test_non_tag_content_and_binary_are_untouched(self):
        for content in (b"[[Wiki Link]]\n", b"\xff\n", b"\0" + TAG.encode(),
                        ("prefix " + TAG + " suffix\n").encode(),
                        ("# " + TAG.replace("2026-09-26", "2026-99-99") + "\n").encode(),
                        ("# " + TAG + " extra\n").encode()):
            self.assertEqual(STRIP.strip_lines(content), content)

    def test_excluded_formats_and_symlinks_are_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            content = ("# " + TAG + "\r\nkeep\r\n").encode()
            paths = []
            for name in ("a.json", "a.JSONL", "uv.lock", "requirements.lock.txt",
                         "bun.lockb", "package-lock.json", "pnpm-lock.yaml", "lockfile", "a.md"):
                path = root / name
                path.write_bytes(content)
                paths.append(path)
            self.assertEqual(STRIP.strip_paths(root, paths), 1)
            self.assertEqual(paths[-1].read_bytes(), b"keep\r\n")
            for path in paths[:-1]:
                self.assertEqual(path.read_bytes(), content)
            with patch.object(Path, "is_symlink", return_value=True):
                self.assertEqual(STRIP.strip_paths(root, paths), 0)
            with patch.object(Path, "resolve", return_value=root.parent / "elsewhere"):
                self.assertEqual(STRIP.strip_paths(root, paths), 0)

    def test_cli_all_and_explicit_paths_only_touch_tracked_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            tracked = root / "tracked.md"
            untracked = root / "untracked.md"
            content = ("<!-- " + TAG + " -->\nkeep\n").encode()
            untracked.write_bytes(content)
            for args in (["--all"], [str(tracked)]):
                tracked.write_bytes(content)
                with patch("sys.argv", ["strip", *args]), patch.object(
                    STRIP.subprocess, "check_output", side_effect=[str(root).encode(), b"tracked.md\0"]
                ), patch("sys.stdout", new_callable=io.StringIO):
                    self.assertEqual(STRIP.main(), 0)
                self.assertEqual(tracked.read_bytes(), b"keep\n")
                self.assertEqual(untracked.read_bytes(), content)
            for invalid in (untracked, root.parent / "outside.md"):
                with patch("sys.argv", ["strip", str(invalid)]), patch.object(
                    STRIP.subprocess, "check_output", side_effect=[str(root).encode(), b"tracked.md\0"]
                ), patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
                    STRIP.main()
            self.assertEqual(untracked.read_bytes(), content)

    def test_cli_requires_paths_or_all(self):
        for args in ([], ["--all", "file.md"]):
            with patch("sys.argv", ["strip", *args]), patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
                STRIP.main()


if __name__ == "__main__":
    unittest.main()
