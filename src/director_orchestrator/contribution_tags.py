# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 09:35 UTC | BEGIN]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 09:25 UTC | BEGIN]]
# [[Cursor Grok 4.6 | cursor-grok-4.6-high-fast | 2026-08-22 | 14:50 UTC | BEGIN]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-22 | 00:50 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-21 | 23:40 UTC | BEGIN]]
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Literal


Boundary = Literal["BEGIN", "END"]
TagPolicy = Literal["inline", "envelope_only", "forbidden"]
DIRECTOR_SYSTEM_LABEL = "director-orchestrator/0.1.0"

TAG_PATTERN = re.compile(
    r"\[\["
    r"(?P<contributor>[^|\]\r\n]{1,80})\s*\|\s*"
    r"(?P<system>[^|\]\r\n]{1,80})\s*\|\s*"
    r"(?P<date>\d{4}-\d{2}-\d{2})\s*\|\s*"
    r"(?P<time>\d{2}:\d{2}) UTC\s*\|\s*"
    r"(?P<boundary>BEGIN|END)"
    r"\]\]"
)

_FORBIDDEN_SUFFIXES = {
    ".7z",
    ".avif",
    ".bmp",
    ".csv",
    ".db",
    ".dll",
    ".docx",
    ".eot",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jar",
    ".jpeg",
    ".jpg",
    ".json",
    ".jsonl",
    ".lock",
    ".map",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".pptx",
    ".pyc",
    ".sqlite",
    ".sqlite3",
    ".sum",
    ".tar",
    ".tsv",
    ".ttf",
    ".webm",
    ".webp",
    ".woff",
    ".woff2",
    ".xlsx",
    ".zip",
}
_FORBIDDEN_NAMES = {
    "cargo.lock",
    "composer.lock",
    "package-lock.json",
    "pipfile.lock",
    "poetry.lock",
    "requirements.lock.txt",
    "uv.lock",
}
# Environment files are deliberately absent: a literal ".env" carries no
# PurePosixPath suffix, so listing ".env" here only ever classified secondary
# files such as "production.env". Both now fall through to "envelope_only" so
# secret-bearing environment files are never asked to carry inline tags.
_INLINE_SUFFIXES = {
    ".c",
    ".cc",
    ".cfg",
    ".conf",
    ".cpp",
    ".css",
    ".example",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".jsx",
    ".md",
    ".mdc",
    ".ps1",
    ".py",
    ".rs",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
_INLINE_NAMES = {
    ".dockerignore",
    ".editorconfig",
    ".gitattributes",
    ".gitignore",
    "dockerfile",
    "makefile",
    "procfile",
}


@dataclass(frozen=True)
class ContributionTag:
    contributor: str
    system: str
    date: str
    time: str
    boundary: Boundary


def neutralize_contribution_tags(text: str) -> str:
    """Defuse embedded contribution tags without touching unrelated text.

    Only a sequence this module would accept as a contribution tag is broken
    apart. Ordinary double-bracket content, and tag-shaped text the parser
    rejects such as an impossible date or a blank identity, keeps its exact
    characters. The neutralized form cannot match the grammar again: the
    boundary keyword no longer abuts a closing ``]]``.

    A rejected candidate resumes the scan just inside its own opening bracket,
    so a real tag hidden behind a decoy prefix is still defused.
    """

    parts: list[str] = []
    position = 0
    while (match := TAG_PATTERN.search(text, position)) is not None:
        if parse_contribution_tag(match.group(0)) is None:
            parts.append(text[position : match.start() + 2])
            position = match.start() + 2
            continue
        parts.append(text[position : match.start()])
        parts.append(f"[ {match.group(0)[1:-1]} ]")
        position = match.end()
    parts.append(text[position:])
    return "".join(parts)


def _safe_label(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 80:
        raise ValueError(f"{field} must contain 1 to 80 characters")
    if any(token in normalized for token in ("|", "\n", "\r", "[[", "]]")):
        raise ValueError(f"{field} contains a reserved contribution-tag token")
    return normalized


def render_contribution_tag(
    contributor: str,
    system: str,
    boundary: Boundary,
    *,
    at: datetime | None = None,
) -> str:
    """Render a stable UTC contribution boundary for a human-visible artifact."""

    if boundary not in {"BEGIN", "END"}:
        raise ValueError("boundary must be BEGIN or END")
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("contribution tag timestamps must be timezone-aware")
    moment = moment.astimezone(timezone.utc)
    return (
        f"[[{_safe_label(contributor, 'contributor')} | "
        f"{_safe_label(system, 'system')} | {moment:%Y-%m-%d} | "
        f"{moment:%H:%M} UTC | {boundary}]]"
    )


def wrap_human_submission(
    text: str,
    contributor: str,
    system: str,
    *,
    at: datetime | None = None,
    slack_markup: bool = False,
    max_length: int | None = None,
    neutralize_embedded_tags: bool = False,
) -> str:
    """Wrap free-form human output without changing structured protocol payloads."""

    body = text.strip()
    if neutralize_embedded_tags:
        body = neutralize_contribution_tags(body)
    begin = render_contribution_tag(contributor, system, "BEGIN", at=at)
    end = render_contribution_tag(contributor, system, "END", at=at)
    if slack_markup:
        begin = f"*{begin}*"
        end = f"*{end}*"
    overhead = len(begin) + len(end) + 4
    if max_length is not None:
        if max_length < overhead:
            raise ValueError("max_length cannot fit contribution boundaries")
        body = body[: max_length - overhead]
    return f"{begin}\n\n{body}\n\n{end}"


def parse_contribution_tag(line: str) -> ContributionTag | None:
    match = TAG_PATTERN.search(line)
    if match is None:
        return None
    try:
        datetime.strptime(
            f"{match.group('date')} {match.group('time')}", "%Y-%m-%d %H:%M"
        )
        contributor = _safe_label(match.group("contributor"), "contributor")
        system = _safe_label(match.group("system"), "system")
    except ValueError:
        return None
    return ContributionTag(
        contributor=contributor,
        system=system,
        date=match.group("date"),
        time=match.group("time"),
        boundary=match.group("boundary"),  # type: ignore[arg-type]
    )


def contribution_tag_policy(path: str) -> TagPolicy:
    """Classify whether inline tags are safe for a repository artifact."""

    normalized = path.replace("\\", "/")
    item = PurePosixPath(normalized)
    name = item.name.lower()
    suffix = item.suffix.lower()
    if name in _FORBIDDEN_NAMES or suffix in _FORBIDDEN_SUFFIXES or ".min." in name:
        return "forbidden"
    if name in _INLINE_NAMES or suffix in _INLINE_SUFFIXES:
        return "inline"
    return "envelope_only"


def validate_inline_contribution_tags(
    path: str,
    text: str,
    *,
    added_lines: list[str] | None = None,
    base_pairs: set[tuple[str, str, str, str]] | None = None,
) -> list[str]:
    """Validate outer contribution boundaries and the current change record.

    ``base_pairs`` carries the matching BEGIN/END identities already present in
    the base tree. Supplying it rejects a file whose outer pair was inherited
    rather than recorded by this change, which Git's rename and copy detection
    cannot catch on its own once a move falls below the similarity threshold and
    is reported as an unrelated delete and add.
    """

    policy = contribution_tag_policy(path)
    tags = [tag for line in text.splitlines() if (tag := parse_contribution_tag(line))]
    if policy == "forbidden":
        return [f"{path}: inline contribution tags are forbidden for this format"] if tags else []
    if policy == "envelope_only":
        return []

    nonblank = [line for line in text.splitlines() if line.strip()]
    if not nonblank:
        return [f"{path}: taggable file is empty"]
    reserved_prefixes = (
        "#!",
        "# -*- coding",
        "# coding:",
        "# coding=",
        "# syntax=",
        "<?xml",
        "<!doctype",
    )
    opening_index = 0
    if PurePosixPath(path).suffix.lower() in {".md", ".mdc"} and nonblank[0].strip() == "---":
        # Treat the opening rule as frontmatter only when a closing rule exists:
        # a lone leading "---" is a horizontal rule, not an unterminated header.
        # Compare stripped lines so a closing rule carrying trailing whitespace
        # still terminates the block. When no closing rule is present the tag
        # requirement below reports the missing BEGIN boundary instead.
        closing_index = next(
            (
                index
                for index in range(1, len(nonblank))
                if nonblank[index].strip() == "---"
            ),
            None,
        )
        if closing_index is not None:
            opening_index = closing_index + 1
    while opening_index < len(nonblank) and nonblank[opening_index].strip().lower().startswith(
        reserved_prefixes
    ):
        opening_index += 1
    begin = (
        parse_contribution_tag(nonblank[opening_index])
        if opening_index < len(nonblank)
        else None
    )
    end = parse_contribution_tag(nonblank[-1])
    errors: list[str] = []
    if begin is None or begin.boundary != "BEGIN":
        errors.append(
            f"{path}: first safe line after any reserved file header must contain a BEGIN contribution tag"
        )
    if end is None or end.boundary != "END":
        errors.append(f"{path}: last nonblank line must contain an END contribution tag")
    if begin is not None and end is not None:
        if (
            begin.contributor,
            begin.system,
            begin.date,
            begin.time,
        ) != (end.contributor, end.system, end.date, end.time):
            errors.append(f"{path}: outer BEGIN and END contribution tags do not match")

    outer_pair: tuple[str, str, str, str] | None = None
    if (
        begin is not None
        and end is not None
        and begin.boundary == "BEGIN"
        and end.boundary == "END"
    ):
        candidate = (begin.contributor, begin.system, begin.date, begin.time)
        if candidate == (end.contributor, end.system, end.date, end.time):
            outer_pair = candidate

    if base_pairs is not None and outer_pair is not None and outer_pair in base_pairs:
        errors.append(
            f"{path}: the outer BEGIN/END contribution pair already exists at the base "
            "commit; this change must record a fresh pair"
        )

    if added_lines is not None:
        added = [tag for line in added_lines if (tag := parse_contribution_tag(line))]
        added_begins = {
            (tag.contributor, tag.system, tag.date, tag.time)
            for tag in added
            if tag.boundary == "BEGIN"
        }
        added_ends = {
            (tag.contributor, tag.system, tag.date, tag.time)
            for tag in added
            if tag.boundary == "END"
        }
        added_pairs = added_begins & added_ends
        if not added_pairs:
            errors.append(
                f"{path}: this change must add one matching BEGIN/END contribution pair"
            )
        elif outer_pair is not None and outer_pair not in added_pairs:
            errors.append(
                f"{path}: the current outer BEGIN/END pair must be the matching pair added by this change"
            )
    return errors
# [[Codex | GPT-5.6 | 2026-08-21 | 23:40 UTC | END]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-22 | 00:50 UTC | END]]
# [[Cursor Grok 4.6 | cursor-grok-4.6-high-fast | 2026-08-22 | 14:50 UTC | END]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 09:25 UTC | END]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 09:35 UTC | END]]
