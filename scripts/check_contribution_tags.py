# [[Claude Code | claude-code/chief-programmer | 2026-08-22 | 00:50 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-21 | 23:40 UTC | BEGIN]]
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from director_orchestrator.contribution_tags import (
    TAG_PATTERN,
    contribution_tag_policy,
    parse_contribution_tag,
    validate_inline_contribution_tags,
)


ZERO_SHA = "0" * 40
GITLINK_MODE = "160000"


def _git(*args: str, binary: bool = False) -> str | bytes:
    result = subprocess.run(
        ["git", *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout if binary else result.stdout.decode("utf-8", errors="replace")


def _effective_base(base: str, head: str) -> str:
    if base and base != ZERO_SHA:
        return base
    for candidate in ("origin/main", "github/main", "main"):
        try:
            return str(_git("merge-base", head, candidate)).strip()
        except subprocess.CalledProcessError:
            continue
    return str(_git("rev-list", "--max-parents=0", head)).splitlines()[0]


def changed_paths(base: str, head: str) -> list[tuple[str, str]]:
    raw = _git("diff", "--name-status", "--find-renames", "-z", base, head, binary=True)
    assert isinstance(raw, bytes)
    fields = raw.decode("utf-8", errors="surrogateescape").split("\0")
    changes: list[tuple[str, str]] = []
    index = 0
    while index < len(fields) and fields[index]:
        status = fields[index]
        index += 1
        if status.startswith(("R", "C")):
            index += 1  # old path
        path = fields[index]
        index += 1
        changes.append((status, path))
    return changes


def _content_at(head: str, path: str) -> bytes:
    value = _git("show", f"{head}:{path}", binary=True)
    assert isinstance(value, bytes)
    return value


def _tree_mode(head: str, path: str) -> str | None:
    """Return the tree mode for a path, or None when it cannot be resolved."""

    try:
        entry = str(_git("ls-tree", head, "--", path))
    except (subprocess.CalledProcessError, UnicodeEncodeError, OSError):
        return None
    return entry.split(" ", 1)[0] if entry.strip() else None


def _added_lines(base: str, head: str, path: str) -> list[str]:
    diff = str(_git("diff", "--unified=0", "--no-color", base, head, "--", path))
    return [
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]


def _validate_submission_envelope(
    text: str, *, require_outer: bool, require_markdown_boundary: bool
) -> list[str]:
    def strict_tag(line: str):
        candidate = line.strip()
        if require_markdown_boundary:
            if not (candidate.startswith("**") and candidate.endswith("**")):
                return None
            candidate = candidate[2:-2]
        if TAG_PATTERN.fullmatch(candidate) is None:
            return None
        return parse_contribution_tag(candidate)

    nonblank = [line for line in text.splitlines() if line.strip()]
    visible_lines = nonblank
    if require_markdown_boundary:
        visible_lines = [
            line
            for line in nonblank
            if not (
                line.strip().startswith("<!--")
                and line.strip().endswith("-->")
            )
        ]
    candidate_lines = visible_lines
    if require_markdown_boundary:
        candidate_lines = [
            line
            for line in visible_lines
            if line.strip().startswith("**[[") and line.strip().endswith("]]**")
        ]
    tags = [tag for line in candidate_lines if (tag := strict_tag(line))]
    begins = {
        (tag.contributor, tag.system, tag.date, tag.time)
        for tag in tags
        if tag.boundary == "BEGIN"
    }
    ends = {
        (tag.contributor, tag.system, tag.date, tag.time)
        for tag in tags
        if tag.boundary == "END"
    }
    if not (begins & ends):
        return ["submission envelope lacks one matching BEGIN/END contribution pair"]
    if require_outer:
        first = strict_tag(visible_lines[0]) if visible_lines else None
        last = strict_tag(visible_lines[-1]) if visible_lines else None
        if first is None or first.boundary != "BEGIN" or last is None or last.boundary != "END":
            return ["submission must begin and end with contribution tags"]
        if require_markdown_boundary and (
            visible_lines[0] not in candidate_lines or visible_lines[-1] not in candidate_lines
        ):
            return ["pull-request body must begin and end with bold contribution tags"]
    return []


def _validate_github_event(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload.get("pull_request"), dict):
        body = payload["pull_request"].get("body") or ""
        return _validate_submission_envelope(
            body, require_outer=True, require_markdown_boundary=True
        )
    if payload.get("ref") == "refs/heads/main":
        # The unchanged PR body was already validated before the protected merge.
        # GitHub synthesizes squash-merge commit text and may omit that envelope.
        return []
    head_commit = payload.get("head_commit")
    message = head_commit.get("message", "") if isinstance(head_commit, dict) else ""
    return _validate_submission_envelope(
        message, require_outer=True, require_markdown_boundary=False
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate contribution tags on the exact changed-file range."
    )
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--github-event", type=Path)
    args = parser.parse_args()

    base = _effective_base(args.base, args.head)
    errors: list[str] = []
    envelope_only: list[str] = []
    forbidden: list[str] = []
    for status, path in changed_paths(base, args.head):
        if status.startswith("D"):
            continue
        if _tree_mode(args.head, path) == GITLINK_MODE:
            # Submodule pointers carry no taggable content of their own; the
            # enclosing submission envelope records the change.
            continue
        policy = contribution_tag_policy(path)
        try:
            content = _content_at(args.head, path)
        except (subprocess.CalledProcessError, UnicodeEncodeError, OSError):
            # Report an unreadable path instead of aborting the whole check with
            # a traceback that hides every other finding in the range.
            errors.append(
                f"{path}: contribution validator could not read this path at the head commit"
            )
            continue
        if b"\0" in content:
            forbidden.append(path)
            continue
        text = content.decode("utf-8", errors="replace")
        if policy == "inline":
            errors.extend(
                validate_inline_contribution_tags(
                    path,
                    text,
                    added_lines=_added_lines(base, args.head, path),
                )
            )
        elif policy == "forbidden":
            forbidden.append(path)
            errors.extend(validate_inline_contribution_tags(path, text))
        else:
            envelope_only.append(path)

    if args.github_event is not None:
        errors.extend(_validate_github_event(args.github_event))

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "Contribution tags valid; "
        f"envelope-only={len(envelope_only)}; inline-forbidden={len(forbidden)}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
# [[Codex | GPT-5.6 | 2026-08-21 | 23:40 UTC | END]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-22 | 00:50 UTC | END]]
