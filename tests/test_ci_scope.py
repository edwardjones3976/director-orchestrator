# [[OpenAI Codex | GPT-6 | 2026-09-26 | 17:17 UTC | BEGIN]]
import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "ci_scope", Path(__file__).resolve().parents[1] / "scripts" / "ci_scope.py"
)
assert SPEC and SPEC.loader
ci_scope = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci_scope)

BEFORE = "a" * 40
AFTER = "b" * 40
BASE = "c" * 40
EVENT = {
    "action": "synchronize",
    "before": BEFORE,
    "after": AFTER,
    "pull_request": {
        "number": 284,
        "head": {"sha": AFTER},
        "base": {"sha": BASE},
    },
}


class CiScopeTests(unittest.TestCase):
    def test_first_run_is_full(self):
        self.assertEqual(
            ci_scope.classify({"action": "opened"}, "token", "owner/repo")[0],
            "full",
        )

    def test_policy_base_merge_after_green_full_run_is_focused(self):
        with (
            patch.object(
                ci_scope,
                "git_output",
                side_effect=[
                    f"{AFTER} {BEFORE} {BASE}\n".encode(),
                    b"AGENTS.md\0docs/contribution-attribution-policy.md\0",
                ],
            ),
            patch.object(ci_scope, "previous_full_run_succeeded", return_value=123),
        ):
            mode, suite, reason = ci_scope.classify(EVENT, "token", "owner/repo")
            self.assertEqual((mode, suite), ("focused", "policy"))
            self.assertIn(f"full run 123 at {BEFORE}", reason)

    def test_no_previous_full_success_falls_back(self):
        with (
            patch.object(
                ci_scope,
                "git_output",
                side_effect=[
                    f"{AFTER} {BEFORE} {BASE}\n".encode(),
                    b"projects/homemade-llm/eval/score_packets.py\0",
                ],
            ),
            patch.object(ci_scope, "previous_full_run_succeeded", return_value=None),
        ):
            self.assertEqual(
                ci_scope.classify(EVENT, "token", "owner/repo")[0], "full"
            )

    def test_unknown_path_or_non_merge_falls_back(self):
        for parents, files in (
            (f"{AFTER} {BEFORE}\n".encode(), b"AGENTS.md\0"),
            (
                f"{AFTER} {BEFORE} {BASE}\n".encode(),
                b".github/workflows/ci.yml\0",
            ),
        ):
            with self.subTest(parents=parents, files=files), patch.object(
                ci_scope, "git_output", side_effect=[parents, files]
            ):
                self.assertEqual(
                    ci_scope.classify(EVENT, "token", "owner/repo")[0], "full"
                )

    def test_full_run_requires_all_jobs_and_full_python_steps(self):
        run = {
            "id": 1,
            "head_sha": BEFORE,
            "conclusion": "success",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "pull_requests": [{"number": 284}],
        }
        jobs = [
            {
                "name": name,
                "conclusion": "success",
                "steps": (
                    [{"name": "Run python -m unittest discover -s tests -v", "conclusion": "success"}]
                    if name.startswith("Python 3.12")
                    else []
                ),
            }
            for name in ci_scope.REQUIRED_JOBS
        ]
        with patch.object(
            ci_scope,
            "api_json",
            side_effect=[{"workflow_runs": [run]}, {"jobs": jobs}],
        ):
            self.assertTrue(
                ci_scope.previous_full_run_succeeded("owner/repo", 284, BEFORE, "token")
            )
        jobs[0]["conclusion"] = "skipped"
        with patch.object(
            ci_scope,
            "api_json",
            side_effect=[{"workflow_runs": [run]}, {"jobs": jobs}],
        ):
            self.assertFalse(
                ci_scope.previous_full_run_succeeded("owner/repo", 284, BEFORE, "token")
            )
        jobs[0]["conclusion"] = "success"
        for job in jobs:
            if job["name"].startswith("Python 3.12"):
                job["steps"] = []
        with patch.object(
            ci_scope,
            "api_json",
            side_effect=[{"workflow_runs": [run]}, {"jobs": jobs}],
        ):
            self.assertFalse(
                ci_scope.previous_full_run_succeeded("owner/repo", 284, BEFORE, "token")
            )


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 17:17 UTC | END]]
