# [[OpenAI Codex | GPT-6 | 2026-09-27 | 19:58 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 19:46 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 11:19 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 10:42 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 09:13 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 09:08 UTC | BEGIN]]
"""Focused active-documentation authority guard; not a runtime attestation.

The pointer/drift check keeps allocation, dispatch, and merge rules in their
canonical homes (AGENTS.md and docs/team-operating-charter.md). Named
instruction surfaces must point there instead of carrying copies that drift.
Owner-local agent files are checked only when passed explicitly with
--local-agent-file; the repository check never reads a home directory.
"""
import argparse
from pathlib import Path
import re

RULES = {
    "obsolete-allocation": re.compile(r"\b25\s*[/,]\s*25\s*[/,]\s*20\s*[/,]\s*15\s*[/,]\s*11\s*[/,]\s*4\b", re.I),
    "legacy-sole-writer": re.compile(r"\blegacy\s+(?:Director\s+)?(?:orchestration\s+engine\s+)?(?:remains|is)\s+(?:the\s+)?sole\s+writer\b", re.I),
    "antigravity-blanket-write-denial": re.compile(r"\b(?:Antigravity|Gemini)\s+(?:may\s+not|cannot|must\s+not)\s+(?:write|edit|modify)\s+(?:any\s+)?(?:project\s+)?(?:files|code)\b", re.I),
    "retired-enterprise-provider": re.compile(r"\bMicrosoft\s*365\s+Copilot\s+(?:is|remains)\s+(?:an?\s+)?(?:active|required)\s+(?:provider|lane)\b", re.I),
    "grok-cursor-alias": re.compile(r"\bGrok\b.{0,40}\bCursor-family\s+peer\b", re.I),
}
BEGIN = re.compile(r"^\s*<!-- director-doc-history BEGIN: (\S.*?) -->\s*$")
END = "<!-- director-doc-history END -->"

LANE = r"(?:OpenAI|Codex|Claude|Cursor|Grok|Gemini|Antigravity|Perplexity|Firecrawl|Copilot)"
COUNT = r"(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
# A copied rule names two or more lanes with a share or a count, restates the
# admission window, band, or ceiling, or repeats a merge/reviewer rule. A single
# number, or one lane named in a technical instruction, is not a copy.
LANE_SHARE = re.compile(rf"\b({LANE})\b[^%\n.;]{{0,40}}?\b\d{{1,3}}(?:\.\d+)?\s*%|\b\d{{1,3}}(?:\.\d+)?\s*%\s+(?:to\s+|for\s+)?({LANE})\b", re.I)
LANE_COUNT = re.compile(rf"\b{COUNT}\s+({LANE})\b", re.I)
DRIFT_RULES = {
    "copied-allocation-window": re.compile(
        r"\b(?:rolling|last|prospective)\s+(?:twenty|nineteen|20|19)(?:-|\s)+assignments?\b"
        r"|\btarget\s*\+\s*\d+\b"
        r"|(?:±|\+/-|plus\s+or\s+minus)\s*(?:\d+|two|three|five)(?:-|\s)*(?:percentage(?:-|\s)+)?points?\b"
        r"|\bfixed\s+denominator\s+of\s+(?:twenty|20)\b",
        re.I,
    ),
    "copied-merge-rule": re.compile(
        r"if they approve it, they should merge it as well"
        r"|each PR should be authored by one person and reviewed by a single reviewer"
        r"|one-non-author-reviewer project-wide"
        r"|\b(?:requires?|needs?|at\s+least|exactly)\s+(?:one|two|three|a\s+single|\d+)\s+"
        r"(?:(?:independent|non-author|different-provider|exact-(?:SHA|head))\s+)*"
        r"(?:reviewers?|reviews|ACCEPT(?:\s+verdicts?|s)?)\b",
        re.I,
    ),
}
POINTER = re.compile(r"\bAGENTS\.md\b|team-operating-charter\.md")
# Git commands and plumbing names (git merge, merge-base, merge-tree) are
# technical instructions, not merge governance.
GOVERNANCE_TOPIC = re.compile(r"\b(?:allocation|dispatch|(?<!git )merge(?!-)|review-lock)\b", re.I)
# Surfaces whose job is to point at the canonical rules; each must carry a pointer.
POINTER_SURFACES = ("CLAUDE.md", "GEMINI.md", ".github/copilot-instructions.md")
# Topic rule files and skills may mention merge but may not copy the rules.
RULE_SURFACE_GLOBS = ((".cursor/rules", "**/*.mdc"), (".agents", "**/*.md"), (".claude", "**/*.md"))


def _active_paragraphs(text: str) -> tuple[list[str], list[str]]:
    """Return history-exemption errors and normalized non-historical paragraphs."""

    errors = []
    active = []
    historical = None
    content = False
    for number, line in enumerate(text.splitlines(), 1):
        if BEGIN.fullmatch(line):
            if historical is not None:
                errors.append(f"line {number}: nested history exemption")
            historical = number
            content = False
            active.append("")
        elif line.strip() == END:
            if historical is None or not content:
                errors.append(f"line {number}: unmatched or empty history exemption")
            historical = None
            active.append("")
        elif "<!-- director-doc-history" in line:
            errors.append(f"line {number}: malformed history exemption")
            active.append(line)
        elif historical is not None:
            content = content or bool(line.strip())
            active.append("")
        else:
            active.append(line)
    if historical is not None:
        errors.append(f"line {historical}: unclosed history exemption")
    # Paragraph boundaries prevent accidental claims assembled from separate prose.
    paragraphs = [
        re.sub(r"\s+", " ", re.sub(r"[*`_]", "", paragraph))
        for paragraph in re.split(r"\n\s*\n", "\n".join(active))
    ]
    return errors, paragraphs


def check_text(text: str) -> list[str]:
    errors, paragraphs = _active_paragraphs(text)
    for normalized in paragraphs:
        for rule, pattern in RULES.items():
            if pattern.search(normalized):
                errors.append(f"{rule}: {normalized[:160]}")
    return errors


def _distinct_lanes(pattern: re.Pattern, paragraph: str) -> set[str]:
    return {
        group.lower()
        for match in pattern.finditer(paragraph)
        for group in match.groups()
        if group
    }


def check_instruction_text(text: str, *, pointer: str = "never") -> list[str]:
    """Report copied allocation/dispatch/merge rules and a missing canonical pointer.

    ``pointer`` is "always" for pointer surfaces, "topic" for local agent files
    (required once the file speaks about allocation, dispatch, or merge), and
    "never" for topic rule files.
    """

    errors, paragraphs = _active_paragraphs(text)
    for normalized in paragraphs:
        if len(_distinct_lanes(LANE_SHARE, normalized)) >= 2:
            errors.append(f"copied-allocation-shares: {normalized[:160]}")
        if len(_distinct_lanes(LANE_COUNT, normalized)) >= 2:
            errors.append(f"copied-dispatch-counts: {normalized[:160]}")
        for rule, pattern in DRIFT_RULES.items():
            if pattern.search(normalized):
                errors.append(f"{rule}: {normalized[:160]}")
    active = "\n".join(paragraphs)
    needs_pointer = pointer == "always" or (pointer == "topic" and GOVERNANCE_TOPIC.search(active))
    if needs_pointer and not POINTER.search(active):
        errors.append("missing-canonical-pointer: reference AGENTS.md or docs/team-operating-charter.md")
    return errors


def _inside_repo(path: Path, root: Path) -> bool:
    try:
        return not path.is_symlink() and path.resolve().is_relative_to(root.resolve())
    except (OSError, RuntimeError):
        return False


def check_tree(root: Path) -> list[str]:
    paths = set()
    errors = []
    for directory in ("docs", "prompts"):
        base = root / directory
        if not _inside_repo(base, root):
            errors.append(f"{directory}: symlink or path outside repository")
            continue
        for extension in ("*.md", "*.yaml", "*.yml"):
            paths.update(base.rglob(extension))
    paths.update(root / name for name in ("README.md", "AGENTS.md", "CLAUDE.md"))
    for path in sorted(paths):
        relative = path.relative_to(root)
        if relative.parts[:2] == ("docs", "archive"):
            continue
        if not _inside_repo(path, root):
            errors.append(f"{relative.as_posix()}: symlink or path outside repository")
            continue
        if not path.is_file():
            continue
        errors.extend(f"{relative.as_posix()}: {error}" for error in check_text(path.read_text(encoding="utf-8")))
    return errors


def _surface_findings(text: str, pointer: str) -> list[str]:
    """Legacy authority rules plus pointer/drift, without repeating history-marker errors."""

    return list(dict.fromkeys(check_text(text) + check_instruction_text(text, pointer=pointer)))


def check_instruction_surfaces(root: Path) -> list[str]:
    surfaces = {root / name: "always" for name in POINTER_SURFACES}
    errors = []
    for directory, pattern in RULE_SURFACE_GLOBS:
        base = root / directory
        if not _inside_repo(base, root):
            errors.append(f"{directory}: symlink or path outside repository")
            continue
        surfaces.update((path, "never") for path in base.glob(pattern) if path not in surfaces)
    projects = root / "projects"
    if not _inside_repo(projects, root):
        errors.append("projects: symlink or path outside repository")
    elif projects.is_dir():
        for project in projects.iterdir():
            if not _inside_repo(project, root):
                errors.append(f"{project.relative_to(root).as_posix()}: symlink or path outside repository")
                continue
            if project.is_dir():
                for name in ("CLAUDE.md", "GEMINI.md"):
                    path = project / name
                    if path.is_file() or path.is_symlink():
                        surfaces[path] = "never"
    for path in sorted(surfaces):
        relative = path.relative_to(root).as_posix()
        if not _inside_repo(path, root):
            errors.append(f"{relative}: symlink or path outside repository")
            continue
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        errors.extend(f"{relative}: {error}" for error in _surface_findings(text, surfaces[path]))
    return errors


def check_local_agent_files(paths: list[Path]) -> list[str]:
    errors = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            errors.append(f"{path}: unreadable local agent file ({type(error).__name__})")
            continue
        errors.extend(f"{path}: {error}" for error in _surface_findings(text, "topic"))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check active documentation and instruction-surface drift.")
    parser.add_argument(
        "--local-agent-file", type=Path, action="append", default=[],
        help="owner-local agent instruction file to check instead of the repository",
    )
    args = parser.parse_args(argv)
    if args.local_agent_file:
        failures = check_local_agent_files(args.local_agent_file)
        passed = f"Local agent-file pointer/drift checks passed for {len(args.local_agent_file)} explicit file(s)."
    else:
        root = Path(__file__).resolve().parents[1]
        # CLAUDE.md is in both sets; report each finding once.
        failures = list(dict.fromkeys(check_tree(root) + check_instruction_surfaces(root)))
        passed = "Active documentation authority and instruction-surface drift checks passed."
    print("\n".join(failures) if failures else passed)
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 09:08 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 09:13 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 10:42 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 11:19 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 19:46 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 19:58 UTC | END]]
