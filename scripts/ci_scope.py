# [[OpenAI Codex | GPT-6 | 2026-09-26 | 17:17 UTC | BEGIN]]
"""Select a narrow CI rerun only after a verified full run and base merge."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


# Only these areas have focused tests and are outside the Docker image and
# recovery probe inputs. Every unmapped path falls back to the full suite.
POLICY_FILES = frozenset(
    {
        ".github/pull_request_template.md",
        "AGENTS.md",
        "docs/contribution-attribution-policy.md",
        "docs/templates/contribution-submission.md",
        "docs/templates/project-bootstrap/review-ready.md",
        "projects/homemade-llm/README.md",
    }
)
SCORER_FILES = frozenset(
    {
        "projects/homemade-llm/eval/score_packets.py",
        "projects/homemade-llm/eval/test_score_packets.py",
    }
)
POLICY_TESTS = (
    "test_contribution_tags.py",
    "test_director_schema.py",
    "test_human_out_of_the_loop_authority.py",
    "test_team_operating_policy.py",
    "test_hotl_execution.py",
    "test_completion_gate_contracts.py",
    "test_provider_profiles.py",
    "test_windows_primary_activation_tools.py",
    "test_packet_task_routing.py",
)
REQUIRED_JOBS = frozenset(
    {
        "Contribution attribution",
        "Secret hygiene",
        "Compose and container build",
        "Cross-process recovery probe",
        "Python 3.12 (ubuntu-latest)",
        "Python 3.12 (windows-latest)",
    }
)
FULL_TEST_STEPS = frozenset(
    {
        "Run python -m unittest discover -s tests -v",
        "python -m unittest discover -s tests -v",
    }
)
SHA = re.compile(r"^[0-9a-f]{40}$")


def api_json(repo: str, path: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def previous_full_run_succeeded(
    repo: str, number: int, before: str, token: str
) -> int | None:
    query = urllib.parse.urlencode(
        {"event": "pull_request", "head_sha": before, "per_page": 30}
    )
    runs = api_json(repo, f"actions/workflows/ci.yml/runs?{query}", token)[
        "workflow_runs"
    ]
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    for run in runs:
        if run.get("head_sha") != before or run.get("conclusion") != "success":
            continue
        if not any(p.get("number") == number for p in run.get("pull_requests", [])):
            continue
        completed = datetime.fromisoformat(run["updated_at"].replace("Z", "+00:00"))
        if completed < cutoff:
            continue
        jobs = api_json(repo, f"actions/runs/{run['id']}/jobs?per_page=100", token)[
            "jobs"
        ]
        by_name = {job["name"]: job for job in jobs}
        if not REQUIRED_JOBS <= by_name.keys():
            continue
        if any(by_name[name].get("conclusion") != "success" for name in REQUIRED_JOBS):
            continue
        if all(
            any(
                step.get("name") in FULL_TEST_STEPS
                and step.get("conclusion") == "success"
                for step in by_name[name].get("steps", [])
            )
            for name in (
                "Python 3.12 (ubuntu-latest)",
                "Python 3.12 (windows-latest)",
            )
        ):
            return int(run["id"])
    return None


def git_output(*args: str) -> bytes:
    return subprocess.run(
        ["git", *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ).stdout


def classify(event: dict[str, Any], token: str, repo: str) -> tuple[str, str, str]:
    if event.get("action") != "synchronize" or not token:
        return "full", "none", "initial run or no Actions read token"
    pull = event.get("pull_request") or {}
    before, after = event.get("before"), event.get("after")
    base = (pull.get("base") or {}).get("sha")
    if not all(isinstance(s, str) and SHA.fullmatch(s) for s in (before, after, base)):
        return "full", "none", "missing commit identity"
    if after != (pull.get("head") or {}).get("sha"):
        return "full", "none", "event head does not match update"
    parents = git_output("rev-list", "--parents", "-n", "1", after).decode().split()
    if parents != [after, before, base]:
        return "full", "none", "update is not a direct base merge"
    files = frozenset(
        os.fsdecode(path)
        for path in git_output(
            "diff", "--no-renames", "--name-only", "-z", before, after
        ).split(b"\0")
        if path
    )
    if files and files <= POLICY_FILES:
        suite = "policy"
    elif files and files <= SCORER_FILES:
        suite = "scorer"
    else:
        return "full", "none", "changed paths lack a focused test map"
    baseline_run = previous_full_run_succeeded(repo, pull["number"], before, token)
    if baseline_run is None:
        return "full", "none", "previous head lacks a green full CI run"
    return "focused", suite, f"{suite} paths; full run {baseline_run} at {before}"


def plan() -> int:
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        mode, suite, reason = classify(
            event, os.environ.get("GH_TOKEN", ""), os.environ["GITHUB_REPOSITORY"]
        )
    except Exception as exc:
        # A selector bug or unavailable API must spend time, never reduce coverage.
        mode, suite, reason = "full", "none", f"scope proof unavailable: {type(exc).__name__}"
    print(f"CI scope: {mode}; {reason}")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"mode={mode}\nsuite={suite}\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as output:
            output.write(f"CI scope: **{mode}** ({reason}).\n")
    return 0


def run_focused(suite_name: str) -> int:
    # `python scripts/ci_scope.py` puts scripts/, not the repository root, on sys.path.
    sys.path.insert(0, str(Path.cwd()))
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    if suite_name == "policy":
        for pattern in POLICY_TESTS:
            suite.addTests(loader.discover("tests", pattern=pattern))
    elif suite_name == "scorer":
        suite.addTests(
            loader.discover(
                "projects/homemade-llm/eval", pattern="test_score_packets.py"
            )
        )
    else:
        raise ValueError(f"unknown focused suite: {suite_name}")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() and result.testsRun > 0 else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["plan"]:
        raise SystemExit(plan())
    if sys.argv[1:] == ["run"]:
        raise SystemExit(run_focused(os.environ["CI_FOCUSED_SUITE"]))
    raise SystemExit("usage: ci_scope.py plan | run")
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 17:17 UTC | END]]
