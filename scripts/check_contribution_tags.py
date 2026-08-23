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

from director_orchestrator.contribution_tags import (
    TAG_PATTERN,
    contribution_tag_policy,
    iter_contribution_tags,
    parse_contribution_tag,
    validate_inline_contribution_tags,
)


ZERO_SHA = "0" * 40
GITLINK_MODE = "160000"
_GIT_TRAILER_LINE = re.compile(r"^(?P<key>[A-Za-z0-9][A-Za-z0-9-]*):[ \t]+\S")
# Only trailers the repository or the hosting platform actually injects after a
# submission envelope are ignored. Anything else is visible content that must
# stay inside the envelope, so trailer-shaped prose such as "Note: ..." keeps
# failing the submission check instead of being silently dropped.
_SUPPORTED_TRAILER_KEYS = frozenset(
    {
        "claude-session",
        "co-authored-by",
        "signed-off-by",
    }
)


def _strict_tag(line: str):
    """Parse a line that is exactly one contribution tag, ignoring surrounding space."""

    candidate = line.strip()
    if TAG_PATTERN.fullmatch(candidate) is None:
        return None
    return parse_contribution_tag(candidate)


def _is_supported_trailer(line: str) -> bool:
    match = _GIT_TRAILER_LINE.match(line.strip())
    return match is not None and match.group("key").lower() in _SUPPORTED_TRAILER_KEYS


def _without_trailing_git_trailers(text: str) -> str:
    """Ignore a strict supported trailer block appended after a commit envelope.

    The block is removed only when every line after the last END contribution
    tag is blank, whitespace-only, or a supported trailer, and the trailers form
    one contiguous run. Trailing blank or whitespace-only lines after that run
    are tolerated: they previously stopped the scan and made a correctly
    trailered commit fail the envelope check. Any other content after the
    envelope is preserved so the validator still reports it.
    """

    lines = text.splitlines()
    end_index = next(
        (
            index
            for index in range(len(lines) - 1, -1, -1)
            if (tag := _strict_tag(lines[index])) is not None and tag.boundary == "END"
        ),
        None,
    )
    if end_index is None:
        return text
    seen_trailer = False
    block_closed = False
    for line in lines[end_index + 1 :]:
        if not line.strip():
            block_closed = seen_trailer
            continue
        if block_closed or not _is_supported_trailer(line):
            return text
        seen_trailer = True
    if not seen_trailer:
        return text
    return "\n".join(lines[: end_index + 1])


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


_RENAME_DETECTION = ("--find-renames", "--find-copies", "--find-copies-harder")


def changed_paths(base: str, head: str) -> list[tuple[str, str | None, str]]:
    """Return (status, old path, new path) for each change in the exact range.

    The old path is retained for rename and copy records so the per-file diff
    below can be taken against the original content. Copy detection is enabled
    so a duplicated taggable file is compared with its source instead of being
    presented as an entirely new file that already carries someone else's tags.
    """

    raw = _git(
        "diff", "--name-status", *_RENAME_DETECTION, "-z", base, head, binary=True
    )
    assert isinstance(raw, bytes)
    fields = raw.decode("utf-8", errors="surrogateescape").split("\0")
    changes: list[tuple[str, str | None, str]] = []
    index = 0
    while index < len(fields) and fields[index]:
        status = fields[index]
        index += 1
        old_path: str | None = None
        if status.startswith(("R", "C")):
            old_path = fields[index]
            index += 1
        path = fields[index]
        index += 1
        changes.append((status, old_path, path))
    return changes


# The base-tree lookup carries no tag grammar of its own. It used to mirror
# TAG_PATTERN as a POSIX ERE, and the copy drifted: it bounded each of the first
# two fields at 80 characters before the delimiter, while TAG_PATTERN accepts an
# 80-character label *plus* the whitespace padding it also allows. A maximum
# length padded tag therefore never reached the parser, so its identity stayed
# invisible to the inherited-pair check and stale reuse of exactly those
# identities passed. Grep instead for the one literal every tag must contain and
# let iter_contribution_tags decide what is a tag. A fixed string cannot drift.
_BASE_TAG_MARKER = "[["


class BaseTagLookupError(RuntimeError):
    """The base tree's contribution tags could not be read."""


def _base_tag_pairs(base: str) -> set[tuple[str, str, str, str]]:
    """Return the matching BEGIN/END identities already present in the base tree.

    Git's rename and copy detection is a similarity heuristic: a logical move or
    copy that is edited past the threshold is reported as an unrelated delete and
    add, and the destination's inherited tags then look like additions. Comparing
    against the identities the base tree already carries rejects that reuse
    without depending on the heuristic.

    The grep is deliberately broad — a fixed-string search for the opening
    bracket pair — and every surfaced line is parsed with the one recognizer the
    rest of the repository uses. Narrowing the search with a second expression
    is what let padded maximum-length identities through before.

    Only exit status 1 means "no matching line". Every other failure, such as an
    unresolvable base revision, is raised: silently returning an empty set would
    disable the reuse check exactly when the range cannot be trusted.
    """

    command = ["git", "grep", "--no-color", "-h", "-I", "-F", _BASE_TAG_MARKER, base]
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as error:
        raise BaseTagLookupError(
            f"could not run git grep against base {base}: {error}"
        ) from error
    if result.returncode == 1:
        return set()
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip().replace("\n", "; ")
        raise BaseTagLookupError(
            f"git grep failed with status {result.returncode} against base {base}"
            f"{': ' + detail if detail else ''}"
        )
    matches = result.stdout.decode("utf-8", errors="replace")
    # Scan line by line: TAG_PATTERN tolerates whitespace around its delimiters,
    # and whitespace includes newlines, so scanning the joined blob could stitch
    # a tag out of two unrelated files' lines.
    tags = [tag for line in matches.splitlines() for tag in iter_contribution_tags(line)]
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
    return begins & ends


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


def _added_lines(
    base: str, head: str, path: str, old_path: str | None = None
) -> list[str]:
    paths = [path] if old_path is None or old_path == path else [old_path, path]
    diff = str(
        _git(
            "diff",
            "--unified=0",
            "--no-color",
            *_RENAME_DETECTION,
            base,
            head,
            "--",
            *paths,
        )
    )
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
        return _strict_tag(candidate)

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
        if (first.contributor, first.system, first.date, first.time) != (
            last.contributor,
            last.system,
            last.date,
            last.time,
        ):
            # An inner matching pair is not enough: the outer boundaries carry
            # the identity of the submission itself, as the file validator
            # already requires.
            return ["submission outer BEGIN and END contribution tags do not match"]
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
        _without_trailing_git_trailers(message),
        require_outer=True,
        require_markdown_boundary=False,
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
    try:
        base_pairs = _base_tag_pairs(base)
    except BaseTagLookupError as error:
        # Fail closed: without the base identities the inherited-pair check
        # cannot run, and the reason must stay visible.
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    errors: list[str] = []
    envelope_only: list[str] = []
    forbidden: list[str] = []
    for status, old_path, path in changed_paths(base, args.head):
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
                    added_lines=_added_lines(base, args.head, path, old_path),
                    base_pairs=base_pairs,
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
# [[Cursor Grok 4.6 | cursor-grok-4.6-high-fast | 2026-08-23 | 08:14 UTC | END]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 09:25 UTC | END]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 09:35 UTC | END]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 09:44 UTC | END]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 10:05 UTC | END]]
