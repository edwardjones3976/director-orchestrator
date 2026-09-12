# [[OpenAI Codex | GPT-6 | 2026-09-12 | 09:08 UTC | BEGIN]]
"""Focused active-documentation authority guard; not a runtime attestation."""
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


def check_text(text: str) -> list[str]:
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
    for paragraph in re.split(r"\n\s*\n", "\n".join(active)):
        normalized = re.sub(r"\s+", " ", re.sub(r"[*`_]", "", paragraph))
        for rule, pattern in RULES.items():
            if pattern.search(normalized):
                errors.append(f"{rule}: {normalized[:160]}")
    return errors


def check_tree(root: Path) -> list[str]:
    paths = set()
    for directory in ("docs", "prompts"):
        paths.update((root / directory).rglob("*.md"))
    paths.update(root / name for name in ("README.md", "AGENTS.md", "CLAUDE.md"))
    errors = []
    for path in sorted(paths):
        relative = path.relative_to(root)
        if not path.is_file() or relative.parts[:2] == ("docs", "archive"):
            continue
        errors.extend(f"{relative.as_posix()}: {error}" for error in check_text(path.read_text(encoding="utf-8")))
    return errors


if __name__ == "__main__":
    failures = check_tree(Path(__file__).resolve().parents[1])
    print("\n".join(failures) if failures else "Active documentation authority checks passed.")
    raise SystemExit(bool(failures))
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 09:08 UTC | END]]
