"""Regression checks for release report parsing."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from review import diffoscope_paths


class DiffoscopePathsTest(unittest.TestCase):
    def test_nested_pretty_print_keeps_the_file_path(self):
        result = {
            "source1": "/left",
            "details": [{
                "source1": "/left/package.json",
                "details": [{
                    "source1": "Pretty-printed",
                    "unified_diff": "-old\n+new\n",
                }],
            }],
        }
        self.assertEqual(
            diffoscope_paths(result, (Path("/left"), Path("/right"))),
            ["package.json"],
        )

    def test_unchanged_control_has_no_findings(self):
        self.assertEqual(diffoscope_paths({}, (Path("/left"), Path("/right"))), [])

    def test_added_directory_file_list_expands_only_added_leaves(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            left_tree = base / "left"
            right_tree = base / "right"
            (left_tree / ".webpack" / "assets").mkdir(parents=True)
            (left_tree / ".webpack" / "assets" / "keep.txt").write_text("same", encoding="utf-8")

            (right_tree / ".webpack" / "assets").mkdir(parents=True)
            (right_tree / ".webpack" / "assets" / "keep.txt").write_text("same", encoding="utf-8")
            (right_tree / ".webpack" / "sfw" / "nested").mkdir(parents=True)
            (right_tree / ".webpack" / "sfw" / "one.js").write_text("1", encoding="utf-8")
            (right_tree / ".webpack" / "sfw" / "nested" / "two.js").write_text("2", encoding="utf-8")

            document = {
                "source1": left_tree.as_posix(),
                "source2": right_tree.as_posix(),
                "details": [{
                    "source1": (left_tree / ".webpack").as_posix(),
                    "source2": (right_tree / ".webpack").as_posix(),
                    "details": [{
                        "source1": "file list",
                        "source2": "file list",
                        "unified_diff": "@@ -1,4 +1,5 @@\n assets\n main\n renderer\n+sfw\n static\n",
                    }],
                }],
            }
            self.assertEqual(
                diffoscope_paths(document, (left_tree, right_tree)),
                [".webpack/sfw/nested/two.js", ".webpack/sfw/one.js"],
            )

    def test_removed_directory_on_left_expands_left_leaves(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            left_tree = base / "left"
            right_tree = base / "right"
            (left_tree / ".webpack" / "legacy").mkdir(parents=True)
            (left_tree / ".webpack" / "legacy" / "a.js").write_text("a", encoding="utf-8")
            (left_tree / ".webpack" / "legacy" / "b.js").write_text("b", encoding="utf-8")
            (right_tree / ".webpack").mkdir(parents=True)

            document = {
                "source1": left_tree.as_posix(),
                "source2": right_tree.as_posix(),
                "details": [{
                    "source1": (left_tree / ".webpack").as_posix(),
                    "source2": (right_tree / ".webpack").as_posix(),
                    "details": [{
                        "source1": "file list",
                        "source2": "file list",
                        "unified_diff": "@@ -1,2 +1 @@\n-legacy\n main\n",
                    }],
                }],
            }
            self.assertEqual(
                diffoscope_paths(document, (left_tree, right_tree)),
                [".webpack/legacy/a.js", ".webpack/legacy/b.js"],
            )

    def test_direct_file_addition_removal_and_whitespace_unicode_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            left_tree = base / "left"
            right_tree = base / "right"
            left_tree.mkdir()
            right_tree.mkdir()
            (left_tree / "old file.txt").write_text("old", encoding="utf-8")
            (right_tree / "new café.txt").write_text("new", encoding="utf-8")

            document = {
                "source1": left_tree.as_posix(),
                "source2": right_tree.as_posix(),
                "details": [{
                    "source1": "file list",
                    "source2": "file list",
                    "unified_diff": "--- left\n+++ right\n@@ -1 +1 @@\n-old file.txt\n+new café.txt\n",
                }],
            }
            self.assertEqual(
                diffoscope_paths(document, (left_tree, right_tree)),
                ["new café.txt", "old file.txt"],
            )

    def test_ordinary_file_diff_not_treated_as_file_list(self):
        document = {
            "source1": "/left",
            "source2": "/right",
            "details": [{
                "source1": "/left/notes.txt",
                "source2": "/right/notes.txt",
                "unified_diff": "@@ -1 +1,2 @@\n keep\n+sfw\n",
            }],
        }
        self.assertEqual(
            diffoscope_paths(document, (Path("/left"), Path("/right"))),
            ["notes.txt"],
        )

    def test_refuses_escaping_absolute_and_link_or_junction_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            left_tree = base / "left"
            right_tree = base / "right"
            left_tree.mkdir()
            right_tree.mkdir()

            for bad_entry in ("../outside", "/etc/passwd", "C:\\Windows\\win.ini"):
                doc = {
                    "source1": "file list",
                    "source2": "file list",
                    "unified_diff": f"@@ -0,0 +1 @@\n+{bad_entry}\n",
                }
                with self.assertRaises(ValueError):
                    diffoscope_paths(doc, (left_tree, right_tree))

            junction_target = right_tree / "linked_dir"
            junction_target.mkdir()
            doc_junction = {
                "source1": "file list",
                "source2": "file list",
                "unified_diff": "@@ -0,0 +1 @@\n+linked_dir\n",
            }
            with patch("review.os.path.isjunction", create=True, side_effect=lambda p: Path(p) == junction_target):
                with self.assertRaises(ValueError):
                    diffoscope_paths(doc_junction, (left_tree, right_tree))

            outside = base / "outside_dir"
            outside.mkdir()
            (outside / "secret.txt").write_text("secret", encoding="utf-8")
            sym_dir = right_tree / "sym_dir"
            try:
                os.symlink(outside, sym_dir)
            except OSError as exc:
                self.skipTest(f"Symbolic links require elevated privilege on this host: {exc}")

            doc_sym = {
                "source1": "file list",
                "source2": "file list",
                "unified_diff": "@@ -0,0 +1 @@\n+sym_dir\n",
            }
            with self.assertRaises(ValueError):
                diffoscope_paths(doc_sym, (left_tree, right_tree))


if __name__ == "__main__":
    unittest.main()
