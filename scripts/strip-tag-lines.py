"""Remove historical standalone tag lines from tracked UTF-8 text files."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

from director_orchestrator.contribution_tags import (
    TAG_PATTERN,
    contribution_tag_policy,
    parse_contribution_tag,
)


WRAPPERS = (
    ("<!--", "-->"), ("/*", "*/"), ("(*", "*)"), ("{#", "#}"),
    ("**", "**"), ("#", ""), ("//", ""), ("--", ""), (";", ""),
    ("%", ""), ("REM ", ""), ("", ""),
)


def strip_lines(content: bytes) -> bytes:
    if b"\0" in content:
        return content
    try:
        content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content
    bom = b"\xef\xbb\xbf" if content.startswith(b"\xef\xbb\xbf") else b""
    kept = []
    for line in content[len(bom):].splitlines(keepends=True):
        text = line.decode("utf-8").strip()
        remove = False
        for start, end in WRAPPERS:
            if not text.startswith(start) or (end and not text.endswith(end)):
                continue
            candidate = text[len(start):len(text) - len(end) if end else None].strip()
            if TAG_PATTERN.fullmatch(candidate) and parse_contribution_tag(candidate):
                remove = True
                break
        if not remove:
            kept.append(line)
    return bom + b"".join(kept)


def strip_paths(root: Path, paths: list[Path]) -> int:
    changed = 0
    for path in paths:
        relative = path.relative_to(root)
        # Resolve every ancestor too: never follow a symlink/junction out of the tree.
        if path.is_symlink() or path.resolve() != path or not path.is_file():
            continue
        lower = relative.as_posix().lower()
        if (
            contribution_tag_policy(lower) == "forbidden"
            or any(suffix in {".json", ".jsonl", ".lock", ".lockb"} for suffix in Path(lower).suffixes)
            or Path(lower).name in {"lock", "lockfile"}
            or "-lock." in Path(lower).name
        ):
            continue
        before = path.read_bytes()
        after = strip_lines(before)
        if before != after:
            path.write_bytes(after)
            changed += 1
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    if args.all == bool(args.paths):
        parser.error("choose paths or --all")
    try:
        root = Path(subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], stderr=subprocess.PIPE
        ).decode("utf-8").strip()).resolve()
        tracked = subprocess.check_output(
            ["git", "-C", str(root), "ls-files", "-z"], stderr=subprocess.PIPE
        ).decode("utf-8").split("\0")
        tracked_paths = {root / name for name in tracked if name}
        selected = tracked_paths if args.all else {Path(os.path.abspath(p)) for p in args.paths}
        if not selected <= tracked_paths:
            parser.error("all paths must be tracked files in the current worktree")
        print(f"Changed {strip_paths(root, sorted(selected))} tracked text files.")
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError):
        print("ERROR: could not read tracked files or complete cleanup", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
