# [[GitHub Copilot | copilot-swe-agent | 2026-09-30 | 03:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:05 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 18:44 UTC | BEGIN]]
# [[Cursor | Composer 2.5 | 2026-09-04 | 18:52 UTC | BEGIN]]
# [[Cursor | Composer 2.5 | 2026-09-04 | 18:46 UTC | BEGIN]]
# [[Cursor | Composer 2.5 | 2026-09-04 | 18:38 UTC | BEGIN]]
# [[Cursor | Composer 2.5 | 2026-09-04 | 18:22 UTC | BEGIN]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 10:05 UTC | BEGIN]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 09:44 UTC | BEGIN]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 09:35 UTC | BEGIN]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 09:25 UTC | BEGIN]]
# [[Cursor Grok 4.6 | cursor-grok-4.6-high-fast | 2026-08-23 | 08:14 UTC | BEGIN]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-22 | 00:50 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-21 | 23:40 UTC | BEGIN]]
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
from director_orchestrator.contribution_tags import TAG_PATTERN  # noqa: E402


ZERO_SHA = "0" * 40


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ).stdout.decode("utf-8", errors="strict")


def validate_commits(base: str, head: str, default_branch_ref: str = "origin/main", *, exclude_default: bool = True) -> list[str]:
    """Validate resolvable commit revisions and their range, excluding fetched main."""
    head = _git("rev-parse", "--verify", "--end-of-options", f"{head}^{{commit}}").strip()
    if base == ZERO_SHA:
        revision_range = head  # New ref: include the root commit too.
    else:
        base = _git("rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}").strip()
        revision_range = f"{base}..{head}"
    try:
        default_head = _git(
            "rev-parse", "--verify", "--end-of-options", f"{default_branch_ref}^{{commit}}"
        ).strip()
    except subprocess.CalledProcessError:
        return [
            "setup error: default-branch ref unavailable; fetch the repository's "
            "default branch and supply --default-branch-ref (default: origin/main)"
        ]
    exclusions = ["--not", default_head] if exclude_default else []
    _git("rev-list", revision_range, *exclusions)  # Validate the actual graph/range too.
    return []


def _validate_github_event(path: Path) -> tuple[list[str], str | None]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("invalid event")
    if "pull_request" in payload:
        pr = payload["pull_request"]
        if not isinstance(pr, dict):
            raise ValueError("invalid pull request")
        body = pr.get("body")
        if body is not None and not isinstance(body, str):
            raise ValueError("invalid body")
        head = pr.get("head")
        sha = head.get("sha") if isinstance(head, dict) else None
        if not isinstance(sha, str) or re.fullmatch(r"[0-9a-fA-F]{40}", sha) is None:
            raise ValueError("pull-request head must identify an exact commit SHA")
        return [], sha
    ref = payload.get("ref")
    if not isinstance(ref, str) or not ref.startswith("refs/"):
        raise ValueError("push event must identify a ref")
    _git("check-ref-format", ref)
    for key in ("before", "after"):
        if key in payload and (not isinstance(payload[key], str)
                or re.fullmatch(r"[0-9a-fA-F]{40}", payload[key]) is None):
            raise ValueError("push event revisions must be exact SHAs")
    return [], payload.get("after")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Git revisions and event structure; contribution formatting is retired.")
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--default-branch-ref", default="origin/main")
    parser.add_argument("--github-event", type=Path)
    args = parser.parse_args()
    try:
        errors = []
        head = args.head
        exclude_default = True
        if args.github_event is not None:
            errors, source_head = _validate_github_event(args.github_event)
            payload = json.loads(args.github_event.read_text(encoding="utf-8"))
            # The fetched main ref already contains a push head; subtracting it
            # would validate no commits. PRs still exclude imported main history.
            exclude_default = "pull_request" in payload or payload.get("ref") != "refs/heads/main"
            if source_head is not None:
                # Never select checkout's synthetic PR merge commit.
                head = source_head
        errors.extend(validate_commits(args.base, head, args.default_branch_ref, exclude_default=exclude_default))
    except (OSError, ValueError, subprocess.CalledProcessError):
        # Never echo commit messages, event payloads or subprocess stderr.
        print("ERROR: could not read the commit range or GitHub event", file=sys.stderr)
        return 1
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        return 1
    print("Git revision range and event structure valid; contribution formatting is not required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# [[Codex | GPT-5.6 | 2026-08-21 | 23:40 UTC | END]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-22 | 00:50 UTC | END]]
# [[Cursor Grok 4.6 | cursor-grok-4.6-high-fast | 2026-08-23 | 08:14 UTC | END]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 09:25 UTC | END]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 09:35 UTC | END]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 09:44 UTC | END]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 10:05 UTC | END]]
# [[Cursor | Composer 2.5 | 2026-09-04 | 18:22 UTC | END]]
# [[Cursor | Composer 2.5 | 2026-09-04 | 18:38 UTC | END]]
# [[Cursor | Composer 2.5 | 2026-09-04 | 18:46 UTC | END]]
# [[Cursor | Composer 2.5 | 2026-09-04 | 18:52 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 18:44 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:05 UTC | END]]
# [[GitHub Copilot | copilot-swe-agent | 2026-09-30 | 03:04 UTC | END]]
