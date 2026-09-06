# [[Claude Code | Claude Fable 5.1 | 2026-09-04 | 20:30 UTC | BEGIN]]
"""Render or check the descriptive provider profile sheets.

Usage (PYTHONPATH=src):

    python scripts/render_provider_profiles.py --check
    python scripts/render_provider_profiles.py --write \
        --contributor "Claude Code" --system "Claude Fable 5.1" \
        --at 2026-09-04T20:30:00Z

``--check`` renders every sheet in memory and compares it with the committed
file after stripping contribution-tag lines; it exits 2 on a validation
problem and 1 when any rendered output is stale or mistagged. ``--write``
regenerates the Markdown sheets, the index, and the schema example, wrapping
each Markdown file in a fresh outer contribution tag pair while preserving the
pairs already inside it. The sheets grant no authority.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from director_orchestrator.provider_profiles import (
    ProviderProfileError,
    TagIdentity,
    check_outputs,
    write_outputs,
)


def _parse_at(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--at must be an ISO-8601 UTC timestamp") from exc
    if moment.tzinfo is None:
        raise argparse.ArgumentTypeError("--at must carry a timezone (use a trailing Z)")
    return moment.astimezone(timezone.utc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="verify rendered outputs are current")
    mode.add_argument("--write", action="store_true", help="regenerate rendered outputs")
    parser.add_argument("--contributor", help="contribution tag contributor label (with --write)")
    parser.add_argument("--system", help="contribution tag system label (with --write)")
    parser.add_argument("--at", type=_parse_at, help="ISO-8601 UTC timestamp for the tag pair (with --write)")
    parser.add_argument("--root", type=Path, default=None, help="profile directory override (tests only)")
    args = parser.parse_args(argv)

    try:
        if args.check:
            problems = check_outputs(root=args.root)
            if problems:
                for problem in problems:
                    print(f"STALE: {problem}", file=sys.stderr)
                return 1
            print("Provider profile sheets are current.")
            return 0
        if not (args.contributor and args.system and args.at):
            parser.error("--write requires --contributor, --system, and --at")
        identity = TagIdentity(contributor=args.contributor, system=args.system, at=args.at)
        written = write_outputs(identity, root=args.root)
    except ProviderProfileError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"Rendered {len(written)} provider profile outputs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
# [[Claude Code | Claude Fable 5.1 | 2026-09-04 | 20:30 UTC | END]]
