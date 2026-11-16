"""Regression checks for release report parsing."""

import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
