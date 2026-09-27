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


ZERO_SHA = "0" * 40
CONTRIBUTOR = re.compile(r"[^|<>\x00-\x1f\x7f]+ \| [^|<>\x00-\x1f\x7f]+")
SOLE_AUTHOR = re.compile(
    r"Sole author: ([^()<>\r\n]+) \(([^<>\r\n]+)\)"
    r"(?:; assistance: ([^,<>\r\n]+(?:, [^,<>\r\n]+)*))?"
)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ).stdout.decode("utf-8", errors="strict")


def validate_commits(base: str, head: str, default_branch_ref: str = "origin/main", *, exclude_default: bool = True) -> list[str]:
    """Check new commits outside fetched default-branch history."""
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
    errors = []
    exclusions = ["--not", default_head] if exclude_default else []
    for commit in _git("rev-list", revision_range, *exclusions).splitlines():
        parents, committer, subject = _git(
            "show", "-s", "--format=%P%n%cn <%ce>%n%s", commit
        ).splitlines()
        if (
            len(parents.split()) == 2
            and committer == "GitHub <noreply@github.com>"
            and re.fullmatch(
                r"Merge pull request #[0-9]+ from .+|"
                r"Merge #[0-9]+: reviewer [a-z][a-z0-9_-]* after merge-preflight \(CI [0-9]+\)",
                subject,
            )
        ):
            continue
        trailers = _git(
            "show", "-s", "--format=%(trailers:key=Contributed-by)", commit
        ).splitlines()
        values = [line.partition(":")[2].strip() for line in trailers if line.strip()]
        if not values or any(
            CONTRIBUTOR.fullmatch(value) is None
            or any(not field.strip() for field in value.split("|"))
            for value in values
        ):
            errors.append(f"{commit}: missing or malformed Contributed-by trailer")
    return errors


def validate_pr_body(body: str) -> list[str]:
    """Require exactly one visible header, not a template, comment or code sample."""
    body = re.sub(r"<!--.*?(?:-->|$)", "", body, flags=re.DOTALL)
    headers = []
    fence = None
    for line in body.splitlines():
        marker = re.match(r"^[ ]{0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not line[marker.end():].strip():
                fence = None
            continue
        if fence is None and line.startswith("Sole author:"):
            headers.append(line)
    if len(headers) == 1:
        match = SOLE_AUTHOR.fullmatch(headers[0])
        if match and all(value.strip() for value in match.groups() if value is not None):
            if match.group(3) is None or all(part.strip() for part in match.group(3).split(",")):
                return []
    return ["PR body requires exactly one Sole author: <assistant> (<model>) header"]


def _validate_github_event(path: Path) -> tuple[list[str], str | None]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("invalid event")
    if "pull_request" in payload:
        pr = payload["pull_request"]
        if not isinstance(pr, dict):
            raise ValueError("invalid pull request")
        body = pr.get("body") or ""
        if not isinstance(body, str):
            raise ValueError("invalid body")
        head = pr.get("head")
        sha = head.get("sha") if isinstance(head, dict) else None
        if not isinstance(sha, str) or re.fullmatch(r"[0-9a-fA-F]{40}", sha) is None:
            raise ValueError("pull-request head must identify an exact commit SHA")
        return validate_pr_body(body), sha
    return [], None  # Push events, including main, require commit trailers only.


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate commit trailers and PR authorship.")
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
    print("Contribution attribution valid: commit trailers and applicable PR header.")
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
